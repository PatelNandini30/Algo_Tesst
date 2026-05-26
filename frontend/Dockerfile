# IMPORTANT: This image expects ./dist to already contain a fresh Vite build.
# A previous attempt to run `npm install && npm run build` inside the image
# OOM-crashed npm on the 16GB HDD host ("Exit handler never called!"), so we
# moved the build step to start.sh (it runs `npm run build` on the host
# before `docker compose up --build`).  If you build the image directly
# (`docker compose build frontend`), run `cd frontend && npm run build` first
# or the bundle will be whatever was last committed to ./dist.

FROM nginx:1.25-alpine
RUN rm -rf /usr/share/nginx/html/*

COPY dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/nginx.conf

EXPOSE 3000
CMD ["nginx", "-g", "daemon off;"]
