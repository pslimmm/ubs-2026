from flask import Flask

app = Flask(__name__)
import routes.square

import importlib
importlib.import_module("routes.kan-cheong-delivery-driver")
