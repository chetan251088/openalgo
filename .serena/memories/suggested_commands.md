# Development Commands for OpenAlgo Market Pulse

## Package Management
```bash
# Install uv (required)
pip install uv

# Install/add a new package
uv add package_name

# Sync dependencies
uv sync
```

## Running the Application
```bash
# Development mode (auto-reloads)
uv run app.py

# Production with Gunicorn (Linux only, use -w 1 for WebSocket)
uv run gunicorn --worker-class eventlet -w 1 app:app
```

## Testing
```bash
# Run all tests
uv run pytest test/ -v

# Run specific test file
uv run pytest test/test_broker.py -v

# Single test function
uv run pytest test/test_broker.py::test_function_name -v

# With coverage
uv run pytest test/ --cov
```

## React Frontend
```bash
cd frontend

# Install dependencies
npm install

# Development server (hot reload)
npm run dev

# Production build
npm run build

# Tests
npm test
npm run test:coverage

# E2E tests
npm run e2e

# Linting and formatting
npm run lint
npm run format
```

## Git and Status
```bash
git status              # Current status
git add <file>          # Stage changes
git commit -m "msg"     # Commit with conventional message
git log                 # View history
```

## Access Points (local dev)
- Main app: http://127.0.0.1:5000
- API docs: http://127.0.0.1:5000/api/docs
- React frontend: http://127.0.0.1:5000/react
