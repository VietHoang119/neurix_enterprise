"""SQL data access on Cloud SQL (SQLAlchemy Core + pg8000).

neurix's repositories (app/repositories/*.py) talk to Supabase through
PostgREST/RPC; the queries here are the same queries expressed in SQL. Every
function takes an open SQLAlchemy Connection so callers control the
transaction boundary.
"""
