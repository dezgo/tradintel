# run.py
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

import os

from app import create_app

app = create_app()

if __name__ == "__main__":
    # Debug mode exposes the Werkzeug interactive debugger, which allows arbitrary
    # code execution on any error page. Keep it OFF by default; opt in explicitly
    # with DEBUG=1 for local development only.
    debug = os.getenv("DEBUG", "0") == "1"
    app.run(debug=debug)
