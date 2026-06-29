import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

from server import app
from a2wsgi import ASGIMiddleware

application = ASGIMiddleware(app)