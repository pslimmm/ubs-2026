from flask import Flask

app = Flask(__name__)
import routes.square
import routes.solve

import importlib
importlib.import_module("routes.kan-cheong-delivery-driver")
