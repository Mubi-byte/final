import React, { useState } from "react";
import toast, { Toaster } from "react-hot-toast";
import { FaFileUpload, FaUserCircle, FaQuestionCircle } from "react-icons/fa";
import thinkTankLogo from "./thinktankblue_logo.png";
//import ReactMarkdown from 'react-markdown';

// Dynamic API Base URL function
const getApiBaseUrl = () => {
  // In development, use localhost:8000
  // In production, use relative paths (empty string)
  return process.env.NODE_ENV === 'development' ? 'http://localhost:8000' : '';
};

export default function App() {
  // State management
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [token, setToken] = useState("");
  const [accessToken, setAccessToken] = useState(null);
  const [show2FA, setShow2FA] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadSuccess, setUploadSuccess] = useState(false);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [messages, setMessages] = useState([
    {
      role: "system",
      content: `Hi I am your trusty RFP/RFQ assistant.\nPlease upload a document.`,
    },
  ]);

  const formatResponse = (content) => (
    <div
      dangerouslySetInnerHTML={{ __html: content }}
      style={{
        fontSize: "14px",
        lineHeight: "1.6",
        color: "#333",
      }}
    />
  );
  
  // Login handler
  const handleLogin = async () => {
    const apiBaseUrl = getApiBaseUrl();
    const res = await fetch(`${apiBaseUrl}/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });

    const data = await res.json();
    if (res.ok) {
      if (data.requires_2fa) {
        setShow2FA(true);
        toast("Enter your 2FA code.");
      } else {
        setAccessToken(data.access_token);
        toast.success("Logged in successfully!");
      }
    } else {
      toast.error(data.detail || "Login failed");
    }
  };

  // 2FA handler
  const handleVerify2FA = async () => {
    const apiBaseUrl = getApiBaseUrl();
    const res = await fetch(`${apiBaseUrl}/2fa/verify`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, token }),
    });

    const data = await res.json();
    if (res.ok) {
      setAccessToken(data.access_token);
      setShow2FA(false);
      toast.success("2FA verified!");
    } else {
      toast.error(data.detail || "2FA failed");
    }
  };

  // File upload handler
  const handleUpload = async (event) => {
    const file = event.target.files[0];
    if (!file || !accessToken) {
      toast.error("Please log in first.");
      return;
    }
    if (!file.name.endsWith(".pdf")) {
      toast.error("Only PDF files are allowed.");
      return;
    }

    setUploading(true);
    setUploadSuccess(false);
    const formData = new FormData();
    formData.append("file", file);

    try {
      const apiBaseUrl = getApiBaseUrl();
      const res = await fetch(`${apiBaseUrl}/upload`, {
        method: "POST",
        body: formData,
      });
      const data = await res.json();

      if (res.ok) {
        setUploadSuccess(true);
        toast.success("Document uploaded!");
      } else {
        toast.error(data.error || "Upload failed");
      }
    } catch (error) {
      toast.error("Upload error: " + error.message);
    } finally {
      setUploading(false);
    }
  };

  // Send message handler
  const sendMessage = async () => {
    if (!input.trim()) return;
    if (!accessToken) {
      toast.error("Please log in first.");
      return;
    }

    const tempInput = input;
    const filteredHistory = messages.filter((msg) => msg.role !== "system");
    const newMessages = [...messages, { role: "user", content: tempInput }];

    setMessages(newMessages);
    setInput("");
    setLoading(true);

    try {
      const apiBaseUrl = getApiBaseUrl();
      const res = await fetch(`${apiBaseUrl}/chat`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${accessToken}`,
        },
        body: JSON.stringify({
          user_input: tempInput,
          history: filteredHistory,
        }),
      });

      const data = await res.json();

      if (res.ok) {
        const reply = data.response?.trim() || "[No response received from assistant.]";
        setMessages([...newMessages, { role: "assistant", content: reply }]);
      } else {
        setMessages([
          ...newMessages,
          { role: "assistant", content: "Error: " + (data.error || "Failed to get response") },
        ]);
      }
    } catch (error) {
      setMessages([
        ...newMessages,
        { role: "assistant", content: "Error: Failed to connect to server." },
      ]);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div
      style={{
        fontFamily: "Arial, sans-serif",
        height: "100vh",
        display: "flex",
        flexDirection: "column",
      }}
    >
      <Toaster position="top-center" />

      <header
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          padding: "10px 20px",
          backgroundColor: "#f0f8ff",
          borderBottom: "1px solid #ccc",
        }}
      >
        <h1 style={{ color: "#1b8ec4", fontSize: "1.5em" }}>
          Think Tank RFP/RFQ Analyser
        </h1>
        <div style={{ display: "flex", alignItems: "center", gap: 20 }}>
          <FaUserCircle size={28} color="#1b8ec4" />
          <img src={thinkTankLogo} alt="Think Tank Logo" style={{ height: 40 }} />
        </div>
      </header>

      <div
        style={{
          padding: 20,
          flex: 1,
          display: "flex",
          flexDirection: "column",
          maxWidth: 900,
          margin: "auto",
          width: "100%",
        }}
      >
        {!accessToken ? (
          <div
            style={{
              backgroundColor: "#f8f9fa",
              padding: "20px",
              borderRadius: "6px",
              marginBottom: "16px",
            }}
          >
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                marginBottom: "16px",
              }}
            >
              <h2
                style={{
                  fontSize: "1.3rem",
                  color: "#1b8ec4",
                  margin: 0,
                }}
              >
                Login to Continue
              </h2>
              <button
                onClick={() =>
                  toast(
                    <div style={{ padding: "8px", maxWidth: "280px" }}>
                      <h3
                        style={{
                          margin: "0 0 8px 0",
                          fontSize: "1rem",
                        }}
                      >
                        Login Help
                      </h3>
                      <p
                        style={{
                          margin: "0 0 8px 0",
                          fontSize: "0.9rem",
                        }}
                      >
                        <strong>Available demo accounts:</strong>
                      </p>
                      <ul
                        style={{
                          paddingLeft: "18px",
                          margin: "8px 0",
                          fontSize: "0.9rem",
                        }}
                      >
                        <li style={{ marginBottom: "4px" }}>
                          <b>Username:</b> alice
                          <br />
                          <b>Password:</b> secret123 (with 2FA)
                        </li>
                        <li>
                          <b>Username:</b> bob
                          <br />
                          <b>Password:</b> password (no 2FA)
                        </li>
                      </ul>
                    </div>,
                    { duration: 8000 }
                  )
                }
                style={{
                  background: "none",
                  border: "none",
                  color: "#1b8ec4",
                  cursor: "pointer",
                  fontSize: "1rem",
                  display: "flex",
                  alignItems: "center",
                  padding: "4px",
                }}
              >
                <FaQuestionCircle style={{ marginRight: "4px" }} /> Help
              </button>
            </div>

            <div
              style={{
                display: "flex",
                gap: "12px",
                marginBottom: "12px",
                flexWrap: "wrap",
              }}
            >
              <input
                placeholder="Username"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                style={{
                  flex: 1,
                  padding: "10px 12px",
                  fontSize: "14px",
                  borderRadius: "4px",
                  border: "1px solid #ddd",
                  minWidth: "180px",
                }}
              />
              <input
                type="password"
                placeholder="Password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                style={{
                  flex: 1,
                  padding: "10px 12px",
                  fontSize: "14px",
                  borderRadius: "4px",
                  border: "1px solid #ddd",
                  minWidth: "180px",
                }}
              />
              <button
                onClick={handleLogin}
                style={{
                  padding: "10px 20px",
                  backgroundColor: "#1b8ec4",
                  color: "#fff",
                  border: "none",
                  borderRadius: "4px",
                  fontSize: "14px",
                  cursor: "pointer",
                  fontWeight: "bold",
                }}
              >
                Sign In
              </button>
            </div>

            {show2FA && (
              <div
                style={{
                  backgroundColor: "#fff",
                  padding: "12px",
                  borderRadius: "4px",
                  border: "1px solid #eee",
                  marginTop: "12px",
                }}
              >
                <h3
                  style={{
                    fontSize: "0.9rem",
                    color: "#1b8ec4",
                    margin: "0 0 8px 0",
                  }}
                >
                  Two-Factor Authentication Required
                </h3>
                <div
                  style={{
                    display: "flex",
                    gap: "8px",
                    alignItems: "center",
                  }}
                >
                  <input
                    placeholder="6-digit code"
                    value={token}
                    onChange={(e) => setToken(e.target.value)}
                    style={{
                      flex: 1,
                      padding: "8px 12px",
                      fontSize: "14px",
                      borderRadius: "4px",
                      border: "1px solid #ddd",
                    }}
                  />
                  <button
                    onClick={handleVerify2FA}
                    style={{
                      padding: "8px 16px",
                      backgroundColor: "#1b8ec4",
                      color: "#fff",
                      border: "none",
                      borderRadius: "4px",
                      fontSize: "14px",
                      cursor: "pointer",
                    }}
                  >
                    Verify
                  </button>
                </div>
                <p
                  style={{
                    fontSize: "0.7rem",
                    color: "#666",
                    margin: "6px 0 0 0",
                  }}
                >
                  Enter code from your authenticator app
                </p>
              </div>
            )}
          </div>
        ) : (
          <>
            <div style={{ marginBottom: "12px" }}>
              <label
                style={{
                  fontWeight: "bold",
                  fontSize: "14px",
                  display: "block",
                  marginBottom: "6px",
                }}
              >
                Upload your RFP document (PDF only):
              </label>
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                }}
              >
                <label
                  htmlFor="file-upload"
                  style={{
                    backgroundColor: "#1b8ec4",
                    color: "#fff",
                    padding: "8px 16px",
                    borderRadius: "4px",
                    cursor: "pointer",
                    display: "flex",
                    alignItems: "center",
                    gap: 6,
                    fontSize: "14px",
                  }}
                >
                  <FaFileUpload size={14} /> Upload PDF
                </label>
                <input
                  id="file-upload"
                  type="file"
                  accept=".pdf"
                  onChange={handleUpload}
                  style={{ display: "none" }}
                />
                {uploading && (
                  <span
                    style={{
                      color: "#666",
                      fontSize: "13px",
                    }}
                  >
                    Uploading...
                  </span>
                )}
                {uploadSuccess && (
                  <span
                    style={{
                      color: "green",
                      fontSize: "13px",
                    }}
                  >
                    ✔ Ready for analysis
                  </span>
                )}
              </div>
            </div>

            <div
              style={{
                flex: 1,
                border: "1px solid #ccc",
                borderRadius: "6px",
                padding: "12px",
                marginBottom: "12px",
                overflowY: "auto",
                backgroundColor: "#fefefe",
              }}
            >
              {messages.map((msg, idx) => (
                <div
                  key={idx}
                  style={{
                    textAlign: msg.role === "user" ? "right" : "left",
                    marginBottom: "12px",
                  }}
                >
                  <div
                    style={{
                      display: "inline-block",
                      backgroundColor: msg.role === "user" ? "#d0ebff" : "#f8f9fa",
                      borderRadius: "6px",
                      padding: "12px",
                      maxWidth: "85%",
                      fontSize: "14px",
                      color: "#333",
                      textAlign: "left",
                      lineHeight: "1.4",
                    }}
                  >
                    {msg.role === "assistant"
                      ? formatResponse(msg.content)
                      : msg.content}
                  </div>
                </div>
              ))}
              {loading && (
                <div
                  style={{
                    fontStyle: "italic",
                    color: "#777",
                    fontSize: "13px",
                    margin: "8px 0 4px 0",
                  }}
                >
                  Assistant is typing...
                </div>
              )}
            </div>

            <div
              style={{
                display: "flex",
                gap: "8px",
              }}
            >
              <input
                type="text"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") sendMessage();
                }}
                placeholder="Type your message..."
                style={{
                  flex: 1,
                  padding: "10px 12px",
                  fontSize: "14px",
                  borderRadius: "4px",
                  border: "1px solid #ddd",
                }}
                disabled={loading || uploading || !uploadSuccess}
              />
              <button
                onClick={sendMessage}
                disabled={loading || uploading || !uploadSuccess}
                style={{
                  padding: "10px 16px",
                  backgroundColor: "#1b8ec4",
                  color: "#fff",
                  border: "none",
                  borderRadius: "4px",
                  fontSize: "14px",
                  cursor: "pointer",
                }}
              >
                Send
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}