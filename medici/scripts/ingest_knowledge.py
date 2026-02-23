#!/usr/bin/env python3
"""Ingest Medici knowledge base markdown files into Neon pgvector.

Reads markdown files from knowledge/, splits by ## headings into chunks,
embeds each chunk with Gemini text-embedding-004, and upserts to the
knowledge_chunks table for RAG retrieval.

Usage:
    python credible/medici/scripts/ingest_knowledge.py
    python credible/medici/scripts/ingest_knowledge.py --dry-run
    python credible/medici/scripts/ingest_knowledge.py --file frameworks/credit-metrics.md
"""

import argparse
import asyncio
import glob
import io
import os
import sys
import time

# Handle Windows UTF-8 output
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from dotenv import load_dotenv
load_dotenv()

import asyncpg
import google.generativeai as genai

# Paths
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
KNOWLEDGE_DIR = os.path.join(SCRIPT_DIR, '..', 'knowledge')

# Gemini embedding model — reduced to 768 dims for HNSW index compatibility
EMBEDDING_MODEL = "models/gemini-embedding-001"
EMBEDDING_DIMS = 768

# Rough token estimation: ~4 chars per token for English text
def estimate_tokens(text: str) -> int:
    return len(text) // 4


def parse_markdown_into_chunks(filepath: str, knowledge_root: str) -> list[dict]:
    """Parse a markdown file into heading-based chunks.

    Each chunk gets the file's title and summary prepended for context.
    The ## Medici Tools section becomes its own chunk.
    """
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    # Relative path from knowledge root for source_file
    rel_path = os.path.relpath(filepath, knowledge_root).replace('\\', '/')

    lines = content.split('\n')

    # Extract title (first # heading) and summary (next non-empty line)
    title = ''
    summary = ''
    title_end_line = 0
    for i, line in enumerate(lines):
        if line.startswith('# ') and not line.startswith('## '):
            title = line.lstrip('# ').strip()
            # Look for summary — next non-empty line after title
            for j in range(i + 1, min(i + 5, len(lines))):
                stripped = lines[j].strip()
                if stripped and not stripped.startswith('#'):
                    summary = stripped
                    title_end_line = j + 1
                    break
            break

    context_prefix = f"# {title}\n\n{summary}\n\n" if title else ""

    # Split by ## headings
    chunks = []
    current_heading = None
    current_lines = []

    for i, line in enumerate(lines):
        if i < title_end_line:
            continue

        if line.startswith('## '):
            # Save previous chunk
            if current_lines:
                chunk_text = '\n'.join(current_lines).strip()
                if chunk_text:
                    full_text = context_prefix + chunk_text if current_heading else chunk_text
                    chunks.append({
                        'source_file': rel_path,
                        'section_heading': current_heading,
                        'chunk_text': full_text,
                        'token_count': estimate_tokens(full_text),
                    })

            current_heading = line.lstrip('# ').strip()
            current_lines = [line]
        else:
            current_lines.append(line)

    # Don't forget the last chunk
    if current_lines:
        chunk_text = '\n'.join(current_lines).strip()
        if chunk_text:
            full_text = context_prefix + chunk_text if current_heading else chunk_text
            chunks.append({
                'source_file': rel_path,
                'section_heading': current_heading,
                'chunk_text': full_text,
                'token_count': estimate_tokens(full_text),
            })

    return chunks


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a batch of texts using Gemini embedding model."""
    result = genai.embed_content(
        model=EMBEDDING_MODEL,
        content=texts,
        task_type="RETRIEVAL_DOCUMENT",
        output_dimensionality=EMBEDDING_DIMS,
    )
    return result['embedding']


async def upsert_chunks(conn: asyncpg.Connection, chunks: list[dict]) -> int:
    """Delete old chunks for affected files and insert new ones."""
    # Get unique source files
    source_files = list({c['source_file'] for c in chunks})

    # Delete existing chunks for these files
    deleted = await conn.execute(
        "DELETE FROM knowledge_chunks WHERE source_file = ANY($1)",
        source_files
    )
    deleted_count = int(deleted.split(' ')[-1]) if deleted else 0

    # Insert new chunks
    await conn.executemany(
        """
        INSERT INTO knowledge_chunks (source_file, section_heading, chunk_text, token_count, embedding)
        VALUES ($1, $2, $3, $4, $5::vector)
        """,
        [
            (
                c['source_file'],
                c['section_heading'],
                c['chunk_text'],
                c['token_count'],
                f"[{','.join(str(v) for v in c['embedding'])}]",
            )
            for c in chunks
        ]
    )

    return deleted_count


async def main():
    parser = argparse.ArgumentParser(description='Ingest knowledge base into pgvector')
    parser.add_argument('--dry-run', action='store_true', help='Parse and embed but do not write to DB')
    parser.add_argument('--file', type=str, help='Ingest a specific file (relative to knowledge/)')
    args = parser.parse_args()

    # Configure Gemini
    api_key = os.environ.get('GEMINI_API_KEY')
    if not api_key:
        print("ERROR: GEMINI_API_KEY not set")
        sys.exit(1)
    genai.configure(api_key=api_key)

    db_url = os.environ.get('DATABASE_URL')
    if not db_url and not args.dry_run:
        print("ERROR: DATABASE_URL not set")
        sys.exit(1)

    # Find markdown files
    knowledge_root = os.path.normpath(KNOWLEDGE_DIR)
    if args.file:
        files = [os.path.join(knowledge_root, args.file)]
        if not os.path.exists(files[0]):
            print(f"ERROR: File not found: {files[0]}")
            sys.exit(1)
    else:
        files = sorted(glob.glob(os.path.join(knowledge_root, '**', '*.md'), recursive=True))

    if not files:
        print("No markdown files found in knowledge/")
        sys.exit(1)

    print(f"{'=' * 60}")
    print(f"KNOWLEDGE BASE INGESTION")
    print(f"{'=' * 60}")
    print(f"Files: {len(files)}")
    print()

    # Parse all files into chunks
    all_chunks = []
    for filepath in files:
        rel = os.path.relpath(filepath, knowledge_root).replace('\\', '/')
        chunks = parse_markdown_into_chunks(filepath, knowledge_root)
        print(f"  {rel}: {len(chunks)} chunks")
        for c in chunks:
            print(f"    - {c['section_heading'] or '(intro)'}: ~{c['token_count']} tokens")
        all_chunks.extend(chunks)

    print()
    print(f"Total chunks: {len(all_chunks)}")
    total_tokens = sum(c['token_count'] for c in all_chunks)
    print(f"Total tokens: ~{total_tokens:,}")
    print(f"Estimated embedding cost: ${total_tokens * 0.00000001:.6f}")
    print()

    if not all_chunks:
        print("No chunks to process.")
        return

    # Embed all chunks
    print("Embedding chunks with Gemini text-embedding-004...")
    start = time.time()
    texts = [c['chunk_text'] for c in all_chunks]

    # Batch in groups of 100 (API limit)
    BATCH_SIZE = 100
    all_embeddings = []
    for i in range(0, len(texts), BATCH_SIZE):
        batch = texts[i:i + BATCH_SIZE]
        embeddings = embed_texts(batch)
        all_embeddings.extend(embeddings)
        if len(texts) > BATCH_SIZE:
            print(f"  Embedded {min(i + BATCH_SIZE, len(texts))}/{len(texts)}")

    elapsed = time.time() - start
    print(f"Embedding complete in {elapsed:.1f}s")
    print(f"Embedding dimensions: {len(all_embeddings[0])}")
    print()

    # Attach embeddings to chunks
    for chunk, embedding in zip(all_chunks, all_embeddings):
        chunk['embedding'] = embedding

    if args.dry_run:
        print("[DRY RUN] Skipping database write.")
        print(f"Would upsert {len(all_chunks)} chunks.")
        return

    # Connect to Neon and upsert
    # Convert SQLAlchemy-style URL to asyncpg format
    conn_url = db_url
    if conn_url.startswith('postgresql+asyncpg://'):
        conn_url = conn_url.replace('postgresql+asyncpg://', 'postgresql://')

    print("Connecting to database...")
    conn = await asyncpg.connect(conn_url, ssl='require')
    try:
        deleted = await upsert_chunks(conn, all_chunks)
        print(f"Deleted {deleted} old chunks")
        print(f"Inserted {len(all_chunks)} new chunks")
    finally:
        await conn.close()

    print()
    print("Done! Knowledge base ingested successfully.")


if __name__ == '__main__':
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
