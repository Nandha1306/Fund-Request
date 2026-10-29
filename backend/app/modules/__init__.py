"""Business modules. Each one owns its tables and exposes: models, schemas, router, service, policies.

Importing this package must register every model on `Base.metadata` (Alembic relies on it),
so each module's models are imported here once they exist.
"""
