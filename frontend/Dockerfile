FROM node:20-alpine

WORKDIR /app

# Copy package files
COPY package.json ./

# Install ALL dependencies including devDependencies
RUN npm install --legacy-peer-deps

# Copy source code
COPY . .

# Build using npx to ensure vite is found
RUN npx vite build

# Expose port
EXPOSE 3000

# Start the production server
CMD ["npx", "vite", "preview", "--host", "0.0.0.0", "--port", "3000"]