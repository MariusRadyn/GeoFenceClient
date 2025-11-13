import firebase_admin
from firebase_admin import credentials, firestore
import datetime

# Path to your service account JSON
cred = credentials.Certificate("/home/geoserver/serviceAccountKey.json")

# Initialize Firebase
firebase_admin.initialize_app(cred)

# Get Firestore client
db = firestore.client()

# Test: write a document
doc_ref = db.collection("test_collection").document("test_doc")
doc_ref.set({
    "message": "Hello Firestore from Raspberry Pi!",
    "timestamp": datetime.datetime.utcnow()
})

# Test: read it back
doc = doc_ref.get()
if doc.exists:
    print("Document read successfully:", doc.to_dict())
else:
    print("Failed to read document")
