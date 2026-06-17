-- Extensions for the local PostgreSQL development database.
--
-- pgvector is enabled now so vector search tables can be added later without
-- changing the local database image or deployment contract.

CREATE EXTENSION IF NOT EXISTS vector;
