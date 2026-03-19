FROM node:20-bullseye AS builder
WORKDIR /app

COPY dist /app/dist

FROM nginx:1.25-alpine
RUN rm -rf /usr/share/nginx/html/*

COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/nginx.conf

EXPOSE 3000
CMD ["nginx", "-g", "daemon off;"]
