FROM node:20-alpine

WORKDIR /app

# Copy package files first for better caching
COPY package.json package-lock.json* ./

# Install dependencies
RUN npm ci --legacy-peer-deps || npm install --legacy-peer-deps

# Copy source code
COPY . .

# Expose port
EXPOSE 3000

# Build the application
RUN npm run build

# Start the production server
CMD ["npm", "run", "preview", "--", "--host", "0.0.0.0", "--port", "3000"]
