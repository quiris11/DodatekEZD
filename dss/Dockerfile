FROM docker.io/eclipse-temurin:25-jdk-jammy

COPY dss-demo-bundle-6.3.zip /opt/
RUN apt-get update && apt-get install -y unzip \
    && unzip /opt/dss-demo-bundle-6.3.zip -d /opt/ \
    && rm /opt/dss-demo-bundle-6.3.zip \
    && chmod +x /opt/dss-demo-bundle-6.3/apache-tomcat-11.0.9/bin/catalina.sh

EXPOSE 8080

RUN useradd -ms /bin/bash dssuser
RUN chown -R dssuser:dssuser /opt/dss-demo-bundle-6.3
USER dssuser

WORKDIR /opt/dss-demo-bundle-6.3/apache-tomcat-11.0.9
CMD ["bin/catalina.sh", "run"]
