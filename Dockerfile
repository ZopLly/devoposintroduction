FROM nginx:stable-alpine
COPY site/ /usr/share/nginx/html
ARG BUILD_SHA=local
RUN printf '%s\n' "$BUILD_SHA" > /usr/share/nginx/html/build-sha.txt
