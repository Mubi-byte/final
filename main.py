from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse, FileResponse
from fastapi import Request
from pydantic import BaseModel
from azure.ai.formrecognizer import DocumentAnalysisClient
from azure.core.credentials import AzureKeyCredential
from azure.storage.blob import BlobServiceClient
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import SearchIndex, SimpleField, SearchableField, SearchFieldDataType 
from fastapi.staticfiles import StaticFiles 
from dotenv import load_dotenv
import os
import openai
from openai import AzureOpenAI
import pyotp
import qrcode
import io
from docx2pdf import convert
import tempfile
from typing import Optional
from datetime import datetime
from dotenv import load_dotenv
from uuid import uuid4


# Load environment variables
load_dotenv()

# Validate required environment variables
required_env_vars = [
    "AZURE_FORM_RECOGNIZER_ENDPOINT",
    "AZURE_FORM_RECOGNIZER_KEY",
    "AZURE_OPENAI_KEY",
    "AZURE_OPENAI_ENDPOINT",
    "AZURE_STORAGE_CONNECTION_STRING",
    "AZURE_STORAGE_CONTAINER",
    "AZURE_SEARCH_SERVICE_NAME",
    "AZURE_SEARCH_ADMIN_KEY",
    "AZURE_SEARCH_INDEX_NAME",
]

missing_vars = [var for var in required_env_vars if not os.getenv(var)]
if missing_vars: 
    raise ValueError(f"Missing required environment variables: {', '.join(missing_vars)}")

app = FastAPI()

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Change in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Azure Form Recognizer setup
form_client = DocumentAnalysisClient(
    endpoint=os.getenv("AZURE_FORM_RECOGNIZER_ENDPOINT"),
    credential=AzureKeyCredential(os.getenv("AZURE_FORM_RECOGNIZER_KEY"))
)

# Initialize Azure OpenAI client
client = AzureOpenAI(
    api_key=os.getenv("AZURE_OPENAI_KEY"),
    api_version="2024-02-15-preview",
    azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT")
)

# Azure Blob Storage setup
blob_service_client = BlobServiceClient.from_connection_string(
    os.getenv("AZURE_STORAGE_CONNECTION_STRING"))
container_client = blob_service_client.get_container_client(
    os.getenv("AZURE_STORAGE_CONTAINER"))

# Azure Cognitive Search
search_service_name = os.getenv("AZURE_SEARCH_SERVICE_NAME")
search_admin_key = os.getenv("AZURE_SEARCH_ADMIN_KEY")
search_index_name = os.getenv("AZURE_SEARCH_INDEX_NAME")
search_endpoint = f"https://{search_service_name}.search.windows.net"
search_credential = AzureKeyCredential(search_admin_key)
search_client = SearchClient(endpoint=search_endpoint, index_name=search_index_name, credential=search_credential)
index_client = SearchIndexClient(endpoint=search_endpoint, credential=search_credential)

def create_search_index():
    fields = [
        SimpleField(name="id", type=SearchFieldDataType.String, key=True),
        SearchableField(name="filename", type=SearchFieldDataType.String),
        SearchableField(name="text", type=SearchFieldDataType.String),
        SimpleField(name="uploaded_at", type=SearchFieldDataType.String)
    ]
    index = SearchIndex(name=search_index_name, fields=fields)

    try:
        existing = [idx.name for idx in index_client.list_indexes()]
        if search_index_name not in existing:
            index_client.create_index(index)
            print(f"✅ Created new index: {search_index_name}")
        else:
            print(f"ℹ️ Index already exists: {search_index_name}")
    except Exception as e:
        print(f"❌ Index creation failed: {e}")

create_search_index()


# Document Store to replace global variable
class DocumentStore:
    def __init__(self):
        self._store = {}

    def get(self, session_id: str = ""):
        return self._store.get(session_id, "")

    def set(self, content: str, session_id: str = ""):
        self._store[session_id] = content

document_store = DocumentStore()

# Fake user DB (demo only)
fake_users_db = {
    "alice": {
        "username": "alice",
        "password": "secret123",
        "two_factor_enabled": True,
        "two_factor_secret": "JBSWY3DPEHPK3PXP",
    },
    "bob": {
        "username": "bob",
        "password": "password",
        "two_factor_enabled": False,
        "two_factor_secret": None,
    },
}

# Request models
class LoginRequest(BaseModel):
    username: str
    password: str

class TwoFAVerifyRequest(BaseModel):
    username: str
    token: str

class ChatInput(BaseModel):
    user_input: str
    history: list = []

async def convert_uploaded_file(file: UploadFile) -> Optional[str]:
    """Convert uploaded Word doc to PDF and return temp PDF path"""
    if not file.filename or not file.filename.lower().endswith(('.docx', '.doc')):
        return None

    temp_docx_path = None
    temp_pdf_path = None
    
    try:
        # Create temp files with proper extensions
        with tempfile.NamedTemporaryFile(suffix='.docx', delete=False) as temp_docx:
            temp_docx_path = temp_docx.name
            
            # Reset file pointer and read content
            await file.seek(0)
            content = await file.read()
            temp_docx.write(content)

        temp_pdf_path = temp_docx_path.replace('.docx', '.pdf')
        
        # Convert to PDF
        convert(temp_docx_path, temp_pdf_path)
        
        # Verify PDF was created
        if not os.path.exists(temp_pdf_path):
            raise Exception("PDF conversion failed - output file not created")
        
        # Clean up the original docx
        os.unlink(temp_docx_path)
        
        return temp_pdf_path
        
    except Exception as e:
        print(f"Conversion error: {str(e)}")
        
        # Clean up temp files
        if temp_docx_path and os.path.exists(temp_docx_path):
            try:
                os.unlink(temp_docx_path)
            except:
                pass
                
        if temp_pdf_path and os.path.exists(temp_pdf_path):
            try:
                os.unlink(temp_pdf_path)
            except:
                pass
                
        return None

@app.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    try:
        # Validate file type first
        if not file.filename:
            return JSONResponse(
                status_code=400,
                content={"error": "No filename provided"}
            )
        
        file_ext = file.filename.lower().split('.')[-1]
        if file_ext not in ('pdf', 'docx', 'doc'):
            return JSONResponse(
                status_code=400,
                content={"error": "Only PDF and Word documents are allowed"}
            )

        # Handle Word documents
        if file_ext in ('docx', 'doc'):
            # Reset file pointer before reading
            await file.seek(0)
            pdf_path = await convert_uploaded_file(file)
            if not pdf_path:
                return JSONResponse(
                    status_code=400,
                    content={"error": "Failed to convert Word document to PDF"}
                )
            
            # Upload the converted PDF
            pdf_filename = f"{file.filename.split('.')[0]}.pdf"
            try:
                with open(pdf_path, 'rb') as pdf_file:
                    blob_client = container_client.get_blob_client(pdf_filename)
                    blob_client.upload_blob(pdf_file.read(), overwrite=True)
                
                # Process with Form Recognizer
                with open(pdf_path, 'rb') as pdf_file:
                    contents = pdf_file.read()
                
                # Clean up temp PDF
                os.unlink(pdf_path)
            except Exception as e:
                # Ensure cleanup happens even if error occurs
                if os.path.exists(pdf_path):
                    os.unlink(pdf_path)
                raise e
        
        # Handle direct PDF uploads
        elif file_ext == 'pdf':
            # Reset file pointer before reading
            await file.seek(0)
            contents = await file.read()
            
            # Upload to blob storage
            blob_client = container_client.get_blob_client(file.filename)
            blob_client.upload_blob(contents, overwrite=True)

        # Process with Form Recognizer
        try:
            poller = form_client.begin_analyze_document("prebuilt-document", contents)
            result = poller.result()
            extracted_text = ""
            
            # Check if result has pages
            if hasattr(result, 'pages') and result.pages:
                for page in result.pages:
                    if hasattr(page, 'lines') and page.lines:
                        for line in page.lines:
                            extracted_text += line.content + "\n"
            
            # Fallback to content if pages not available
            if not extracted_text and hasattr(result, 'content'):
                extracted_text = result.content
            
            if not extracted_text:
                return JSONResponse(
                    status_code=400,
                    content={"error": "No text could be extracted from the document"}
                )
        
        except Exception as form_error:
            print(f"Form Recognizer error: {str(form_error)}")
            return JSONResponse(
                status_code=500,
                content={"error": f"Document analysis failed: {str(form_error)}"}
            )

        # Store in document store
        document_store.set(extracted_text)

        # Index in Azure Search (with error handling)
        try:
            doc_id = str(uuid4())
            document = {
                "id": doc_id,
                "filename": file.filename,
                "text": extracted_text[:50000],  # Truncate if too long
                "uploaded_at": datetime.utcnow().isoformat()
            }
            upload_result = search_client.upload_documents(documents=[document])
            if upload_result and len(upload_result) > 0 and not upload_result[0].succeeded:
                print(f"Warning: Indexing failed: {upload_result[0].error_message}")
        except Exception as search_error:
            print(f"Search indexing error: {str(search_error)}")
            # Don't fail the entire upload if search indexing fails
        
        return JSONResponse(
            status_code=200,
            content={
                "message": "File uploaded and processed successfully",
                "filename": file.filename,
                "text_length": len(extracted_text)
            }
        )

    except Exception as e:
        print(f"Upload error: {str(e)}")
        return JSONResponse(
            status_code=500,
            content={"error": f"Processing failed: {str(e)}"}
        )

# Authentication Endpoints
@app.post("/login")
async def login(req: LoginRequest):
    user = fake_users_db.get(req.username)
    if not user or user["password"] != req.password:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    if user["two_factor_enabled"]:
        return {"requires_2fa": True, "two_factor_enabled": True}
    return {"access_token": f"dummy_token_for_{user['username']}", "two_factor_enabled": False}

@app.post("/2fa/verify")
async def verify_2fa(req: TwoFAVerifyRequest):
    user = fake_users_db.get(req.username)
    if not user or not user["two_factor_enabled"]:
        raise HTTPException(status_code=400, detail="2FA not enabled or user not found")
    totp = pyotp.TOTP(user["two_factor_secret"])
    if not totp.verify(req.token):
        raise HTTPException(status_code=401, detail="Invalid 2FA token")
    return {"access_token": f"dummy_token_for_{user['username']}"}

@app.get("/2fa/setup")
async def setup_2fa(username: str):
    user = fake_users_db.get(username)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if not user.get("two_factor_secret"):
        secret = pyotp.random_base32()
        user["two_factor_secret"] = secret
    totp = pyotp.TOTP(user["two_factor_secret"])
    uri = totp.provisioning_uri(name=username, issuer_name="RFP Assistant")
    qr = qrcode.make(uri)
    buf = io.BytesIO()
    qr.save(buf, format="PNG")
    buf.seek(0)
    return StreamingResponse(buf, media_type="image/png")

# Chat Endpoint
@app.post("/chat")
async def chat_endpoint(data: ChatInput):
    messages = data.history or []

    if not any(msg["role"] == "system" for msg in messages):
        messages.insert(0, {
            "role": "system",
            "content": (
    """You are an AI assistant helping the Think Tank Sales Team analyze RFP/RFQ documents. 
    Always provide specific, relevant information and cite the section of the document where you found the information. 
    You are an RFP analysis assistant. When you answer, use HTML for formatting: 
    - Use <b> for bold, <ul>/<li> for lists, and <p> for paragraphs. 
    - Do not use asterisks or markdown. 
    - Make your answers readable and well-structured. 
    Focus on: 
    1. Technology requirements and specifications 
    2. User numbers and deployment scale 
    3. Current systems and pain points 
    4. Required integrations and platforms 
    5. Matching with past proposals and BOMs 
    6. Identifying compliance requirements 
    7. Suggesting actionable tasks for the proposal team. 
    If suggesting BOM items, explain why they match the requirements. 
    Always cite the section of the document where you found the information."""
            )
        })

    # Add extracted document text as context if available
    document_context = document_store.get()
    if document_context:
        messages.insert(1, {
            "role": "system",
            "content": f"Here is the extracted document content for reference:\n{document_context}"
        })

    messages.append({"role": "user", "content": data.user_input})

    try:
        print("🔍 Sending to Azure OpenAI:", messages)
        response = client.chat.completions.create(
            model=os.getenv("AZURE_OPENAI_DEPLOYMENT"),
            messages=messages,
            temperature=0.5,
            max_tokens=1000,
            top_p=1,
            frequency_penalty=0.2,
            presence_penalty=0.3,
        )

        reply = response.choices[0].message.content.strip()
        print("✅ Received reply:", reply)
        return {"response": reply}

    except openai.BadRequestError as e:
        return JSONResponse(status_code=400, content={"error": f"Invalid request to OpenAI: {str(e)}"})
    except openai.AuthenticationError as e:
        return JSONResponse(status_code=401, content={"error": "Authentication failed with OpenAI"})
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": f"Internal server error: {str(e)}"})

# Debug endpoint (optional)
@app.get("/debug/env")
async def debug_env():
    return {
        var: bool(os.getenv(var)) for var in required_env_vars
    }

# Serve static files if frontend exists
if os.path.exists("frontend/build/static"):
    app.mount("/static", StaticFiles(directory="frontend/build/static"), name="static")

@app.get("/{full_path:path}")
async def serve_frontend(full_path: str):
    # Serve API routes normally
    if full_path.startswith("api/"):
        return JSONResponse({"error": "API route not found"}, status_code=404)
    
    # Handle root path or empty path
    if not full_path or full_path == "/":
        if os.path.exists("frontend/build/index.html"):
            return FileResponse("frontend/build/index.html")
        else:
            return JSONResponse({"error": "Frontend not found"}, status_code=404)
    
    # Build the file path
    file_path = f"frontend/build/{full_path}"
    
    # Only serve if it's actually a file (not a directory)
    if os.path.exists(file_path) and os.path.isfile(file_path):
        return FileResponse(file_path)
    
    # For any unmatched route, default to serving index.html (SPA routing)
    if os.path.exists("frontend/build/index.html"):
        return FileResponse("frontend/build/index.html")
    else:
        return JSONResponse({"error": "Frontend not found"}, status_code=404)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
