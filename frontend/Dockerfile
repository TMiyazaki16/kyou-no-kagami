# きょうのかがみ フロントエンド
# 1) node でビルド（tsc の型チェック込み） 2) nginx で dist を配信し、/api と /uploads を backend へプロキシ
FROM node:20-alpine AS build
WORKDIR /src
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /src/dist /usr/share/nginx/html
EXPOSE 80
