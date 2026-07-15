import requests
import json
import sys

def test_integration():
    fastapi_url = "http://localhost:8000"
    repo_id = "test_integration_repo_999"

    print("🚀 Running end-to-end RAG integration test...")

    # Mock files to index
    payload = {
        "repoId": repo_id,
        "files": [
            {
                "path": "services/user_service.py",
                "content": """
import os

class UserService:
    def __init__(self, db_conn):
        self.db = db_conn

    def get_user(self, user_id):
        # Retrieve user from DB
        return self.db.query(user_id)

    def delete_user(self, user_id):
        self.db.delete(user_id)
"""
            },
            {
                "path": "utils/jwt_helper.js",
                "content": """
const jwt = require('jsonwebtoken');

function signToken(payload) {
    return jwt.sign(payload, 'secret_key', { expiresIn: '1h' });
}

function verifyToken(token) {
    return jwt.verify(token, 'secret_key');
}

module.exports = { signToken, verifyToken };
"""
            }
        ]
    }

    # 1. Test indexing (POST /api/chatbot/embeddings)
    print("\n1. Indexing mock repository files...")
    try:
        r = requests.post(f"{fastapi_url}/api/chatbot/embeddings", json=payload, timeout=120)
        print(f"   Status Code: {r.status_code}")
        print(f"   Response: {json.dumps(r.json(), indent=2)}")
        
        # Verify response structure
        res_data = r.json()
        assert "totalChunks" in res_data, "Expected totalChunks in response"
        assert res_data.get("fileCount") == 2, f"Expected 2 files, got {res_data.get('fileCount')}"
        print("   ✅ Indexing response schema validated successfully.")
    except Exception as e:
        print(f"   ❌ Indexing failed: {e}")
        sys.exit(1)

    # 2. Test status check (GET /api/chatbot/embeddings/:repoId)
    print("\n2. Checking indexing status...")
    try:
        r = requests.get(f"{fastapi_url}/api/chatbot/embeddings/{repo_id}", timeout=10)
        print(f"   Status Code: {r.status_code}")
        print(f"   Response: {json.dumps(r.json(), indent=2)}")
        
        # Verify status details
        res_data = r.json()
        assert res_data.get("exists") is True, "Expected exists to be True"
        print("   ✅ Status response validated successfully.")
    except Exception as e:
        print(f"   ❌ Status check failed: {e}")
        sys.exit(1)

    # 3. Test chat endpoint (POST /api/chatbot/chat)
    print("\n3. Testing chatbot query...")
    chat_payload = {
        "repoId": repo_id,
        "query": "Where is JWT token verified?",
        "provider": "groq"
    }
    try:
        r = requests.post(f"{fastapi_url}/api/chatbot/chat", json=chat_payload, timeout=120)
        print(f"   Status Code: {r.status_code}")
        print(f"   Response: {json.dumps(r.json(), indent=2)}")
        
        res_data = r.json()
        if r.status_code == 200:
            # Verify backward compatible fields exist
            assert "answer" in res_data, "Expected answer field"
            assert "relevantFiles" in res_data, "Expected relevantFiles field"
            assert "confidence" in res_data, "Expected confidence field"
            print("   ✅ Chat response backward compatibility validated successfully.")
        elif r.status_code == 400 and "GROQ_API_KEY is not configured" in res_data.get("detail", ""):
            print("   ✅ Chat endpoint correctly rejected query with 400 and Groq API key warning (Offline mode).")
        else:
            print(f"   ❌ Chat returned unexpected status: {r.status_code}")
            sys.exit(1)
    except Exception as e:
        print(f"   ❌ Chat failed: {e}")
        sys.exit(1)

    # 4. Cleanup embeddings (DELETE /api/chatbot/embeddings/:repoId)
    print("\n4. Cleaning up index...")
    try:
        r = requests.delete(f"{fastapi_url}/api/chatbot/embeddings/{repo_id}", timeout=10)
        print(f"   Status Code: {r.status_code}")
        print(f"   Response: {json.dumps(r.json(), indent=2)}")
        assert r.json().get("success") is True, "Expected cleanup success"
        print("   ✅ Cleanup verified.")
    except Exception as e:
        print(f"   ❌ Cleanup failed: {e}")
        sys.exit(1)

    print("\n🎉 All integration checks passed successfully!")

if __name__ == "__main__":
    test_integration()
