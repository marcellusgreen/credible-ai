"""Add knowledge_chunks table with pgvector for RAG retrieval

Enables the pgvector extension on Neon and creates a table to store
embedded knowledge chunks from Medici's markdown knowledge base.
Used for retrieval-augmented generation in the chat route.

Revision ID: 027_add_knowledge_chunks
Revises: 026_add_benchmark_total_debt
Create Date: 2026-02-22

"""
from alembic import op

# revision identifiers, used by Alembic.
revision = '027_add_knowledge_chunks'
down_revision = '026_add_benchmark_total_debt'
branch_labels = None
depends_on = None


def upgrade():
    # Enable pgvector extension (Neon supports this natively)
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # Knowledge chunks table for RAG retrieval
    op.execute("""
        CREATE TABLE knowledge_chunks (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            source_file     VARCHAR(255) NOT NULL,
            section_heading VARCHAR(255),
            chunk_text      TEXT NOT NULL,
            token_count     INTEGER NOT NULL,
            embedding       vector(768) NOT NULL,
            created_at      TIMESTAMPTZ DEFAULT now(),
            updated_at      TIMESTAMPTZ DEFAULT now()
        )
    """)

    # HNSW index for fast approximate nearest neighbor search
    op.execute("""
        CREATE INDEX idx_knowledge_chunks_embedding
            ON knowledge_chunks USING hnsw (embedding vector_cosine_ops)
    """)

    # Index for looking up by source file (for re-ingestion)
    op.execute("""
        CREATE INDEX idx_knowledge_chunks_source
            ON knowledge_chunks (source_file)
    """)


def downgrade():
    op.execute("DROP TABLE IF EXISTS knowledge_chunks")
    # Don't drop the vector extension — other tables might use it
