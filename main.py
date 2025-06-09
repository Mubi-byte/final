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

def convert_uploaded_file(file: UploadFile) -> Optional[str]:
    """Convert uploaded Word doc to PDF and return temp PDF path"""
    if not file.filename.lower().endswith(('.docx', '.doc')):
        return None

    try:
        # Create temp files
        with tempfile.NamedTemporaryFile(suffix='.docx', delete=False) as temp_docx:
            temp_docx_path = temp_docx.name
            temp_docx.write(file.file.read())

        temp_pdf_path = temp_docx_path.replace('.docx', '.pdf')
        
        # Convert to PDF
        convert(temp_docx_path, temp_pdf_path)
        
        # Clean up the original docx
        os.unlink(temp_docx_path)
        
        return temp_pdf_path
    except Exception as e:
        print(f"Conversion error: {str(e)}")
        if 'temp_docx_path' in locals() and os.path.exists(temp_docx_path):
            os.unlink(temp_docx_path)
        if 'temp_pdf_path' in locals() and os.path.exists(temp_pdf_path):
            os.unlink(temp_pdf_path)
        return None

@app.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    file_ext = file.filename.lower().split('.')[-1]
    
    try:
        # Handle Word documents
        if file_ext in ('docx', 'doc'):
            pdf_path = convert_uploaded_file(file)
            if not pdf_path:
                return JSONResponse(
                    status_code=400,
                    content={"error": "Failed to convert Word document to PDF"}
                )
            
            # Upload the converted PDF
            pdf_filename = f"{file.filename.split('.')[0]}.pdf"
            with open(pdf_path, 'rb') as pdf_file:
                blob_client = container_client.get_blob_client(pdf_filename)
                blob_client.upload_blob(pdf_file.read(), overwrite=True)
            
            # Process with Form Recognizer
            with open(pdf_path, 'rb') as pdf_file:
                contents = pdf_file.read()
            
            # Clean up temp PDF
            os.unlink(pdf_path)
        
        # Handle direct PDF uploads
        elif file_ext == 'pdf':
            contents = await file.read()
            blob_client = container_client.get_blob_client(file.filename)
            blob_client.upload_blob(contents, overwrite=True)
        else:
            return JSONResponse(
                status_code=400,
                content={"error": "Only PDF and Word documents are allowed"}
            )

        # Process with Form Recognizer
        poller = form_client.begin_analyze_document("prebuilt-document", contents)
        result = poller.result()
        extracted_text = ""
        for page in result.pages:
            for line in page.lines:
                extracted_text += line.content + "\n"
        
        # Store in document store
        document_store.set(extracted_text)

        # Also index in Azure Search
        doc_id = str(uuid4())
        document = {
            "id": doc_id,
            "filename": file.filename,
            "text": extracted_text,
            "uploaded_at": datetime.utcnow().isoformat()
        }
        upload_result = search_client.upload_documents(documents=[document])
        if not upload_result[0].succeeded:
            print(f"Warning: Indexing failed: {upload_result[0].error_message}")

        return {"message": "File uploaded and processed successfully."}

    except Exception as e:
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
if os.path.exists("frontend/build"):
    app.mount("/static", StaticFiles(directory="frontend/build/static"), name="static")

@app.get("/{full_path:path}")
async def serve_frontend(full_path: str):
    # Serve API routes normally
    if full_path.startswith("api/"):
        return JSONResponse({"error": "API route not found"}, status_code=404)
    
    file_path = f"frontend/build/{full_path}"
    if os.path.exists(file_path):
        return FileResponse(file_path)
    
    # Default to serving the index.html for any unmatched route
    if os.path.exists("frontend/build/index.html"):
        return FileResponse("frontend/build/index.html")
    else:
        return JSONResponse({"error": "Frontend not found"}, status_code=404)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)