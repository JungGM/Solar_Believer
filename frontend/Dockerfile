FROM nginx:1.28-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
RUN rm -f /usr/share/nginx/html/index.html /usr/share/nginx/html/50x.html
EXPOSE 80
