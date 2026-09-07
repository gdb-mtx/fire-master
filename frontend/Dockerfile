# Dev-server image for the React frontend. Mirrors how start.sh runs it natively
# (`npm run dev`), but inside a container so users never install Node.
FROM node:22.22-slim@sha256:e21fc383b50d5347dc7a9f1cae45b8f4e2f0d39f7ade28e4eef7d2934522b752

WORKDIR /app/frontend

# Require the committed lockfile and reproduce it exactly.
COPY package.json package-lock.json ./
RUN npm ci

# Copy the rest of the app (node_modules excluded by .dockerignore)
COPY --chown=node:node . .
RUN chown -R node:node /app/frontend/node_modules

EXPOSE 5173

# --host binds 0.0.0.0 so the dev server is reachable from the host browser.
# Without it, Vite binds localhost INSIDE the container and :5173 is unreachable.
USER node
CMD ["npm", "run", "dev", "--", "--host", "--port", "5173"]
