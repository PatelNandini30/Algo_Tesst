# Stage 1: Build the React app
FROM node:20 AS builder

WORKDIR /app

# Disable SSL verification for corporate/ISP proxy environments
RUN npm config set strict-ssl false

# Copy package files first for better layer caching
COPY package.json package-lock.json ./

# Install dependencies
RUN npm install --legacy-peer-deps

# Copy source code after npm install
COPY . .

# Build using local vite binary directly
RUN ./node_modules/.bin/vite build

# Stage 2: Serve with nginx (Debian-based, no Alpine)
FROM nginx:bookworm-slim

# Copy only built static files from Stage 1
COPY --from=builder /app/dist /usr/share/nginx/html

# Copy nginx config
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 3000

CMD ["nginx", "-g", "daemon off;"]