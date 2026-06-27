import uvicorn

from image_gen.web import app

if __name__ == "__main__":
    print("Starting Match Postcard Generator at http://127.0.0.1:8000")
    uvicorn.run(app, host="127.0.0.1", port=8000)