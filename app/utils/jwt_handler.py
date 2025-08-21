from fastapi import Depends, HTTPException, Header
import jwt, os

JWT_SECRET = os.getenv("LUNA_JWT_SECRET", "changeme")

def decode_jwt(authorization: str = Header(...)):
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid header")
    token = authorization.split(" ")[1]
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        return payload
    except Exception as e:
        raise HTTPException(status_code=401, detail="Invalid token")
