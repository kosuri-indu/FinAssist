from flask_sqlalchemy import SQLAlchemy
from flask import Flask
import os
from sqlalchemy.pool import NullPool

db = SQLAlchemy()


def init_db(app: Flask):
        # Prefer a URI already set on the Flask app (e.g., fallback chosen in app.py),
        # otherwise use env or a local sqlite default for development.
        database_url = app.config.get('SQLALCHEMY_DATABASE_URI') or os.environ.get('DATABASE_URL') or 'sqlite:///dev.db'
        app.config['SQLALCHEMY_DATABASE_URI'] = database_url
        app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

        engine_opts = {
                'pool_size': int(os.environ.get('DB_POOL_SIZE', 3)),
                'max_overflow': int(os.environ.get('DB_MAX_OVERFLOW', 2)),
                'pool_timeout': int(os.environ.get('DB_POOL_TIMEOUT', 30)),
                'pool_pre_ping': True,
        }

        if os.environ.get('USE_NULLPOOL') == '1':
                engine_opts = {'poolclass': NullPool, 'pool_pre_ping': True}

        app.config['SQLALCHEMY_ENGINE_OPTIONS'] = engine_opts

        db.init_app(app)
        return db