# Build multi-stage: node compila a estático, nginx lo sirve.
# Esqueleto mínimo (§8 de doc/arquitectura/01_design.md) — el despliegue real
# se define en doc/arquitectura/03_deplyment_design.md, fuera del alcance de F5.

FROM node:20-alpine AS build
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install
COPY . .
RUN npm run build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
