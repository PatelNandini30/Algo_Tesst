FROM node:20-bullseye AS builder
WORKDIR /app
RUN npm config set strict-ssl false
COPY package.json package-lock.json ./
RUN npm install --legacy-peer-deps
COPY . .
RUN ./node_modules/.bin/vite build

FROM nginx:bullseye
COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 3000
CMD ["nginx", "-g", "daemon off;"]
