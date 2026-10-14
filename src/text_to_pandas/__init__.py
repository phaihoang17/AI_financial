"""Text-to-Pandas query path (ADR-069).

Natural-language question -> dataset/schema discovery -> LLM-generated Pandas
code -> AST validation -> isolated Pandas worker -> bounded repair -> result
verification -> grounded answer. No symbolic DSL is generated or interpreted.
"""
