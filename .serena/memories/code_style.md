# Code Style and Conventions

## Python
- Follow PEP 8 style guide
- Use 4 spaces for indentation
- Use Google-style docstrings
- Imports: Standard library → Third-party → Local
- Type hints preferred for function signatures
- Use descriptive names for functions/variables

## React/TypeScript
- Follow Biome.js linting rules (frontend/biome.json)
- Use functional components with hooks
- Component files: PascalCase (MyComponent.tsx)
- State management: Zustand for stores

## Git Commit Messages (Conventional Commits)
- `feat:` New features
- `fix:` Bug fixes
- `docs:` Documentation changes
- `refactor:` Code refactoring
- `test:` Test additions
- `chore:` Build/tooling changes

## Services Layer Pattern
- Services live in `services/` directory
- Implement business logic
- Use SQLAlchemy ORM for database queries (no raw SQL)
- Return consistent JSON: `{'status': 'success'|'error', 'message': '...', 'data': {...}}`
- Use logging module for debug/info/warning

## LLM Provider Pattern (Task 8)
- Define abstract `LLMProvider` base class
- Implement concrete providers (Claude, OpenAI, Gemini, Ollama)
- Use factory pattern for provider instantiation
- Fall back gracefully if LLM unavailable
- Cache results to avoid repeated LLM calls
- Non-blocking API design preferred
