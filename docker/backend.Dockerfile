# Runs the application's PREBUILT backend JAR (an input artifact, never built here) for API tests.
# The JAR comes from the named build context `backend_artifact` (a directory containing backend.jar),
# e.g. `docker build --build-context backend_artifact=artifacts/backend -f docker/backend.Dockerfile docker`.
FROM eclipse-temurin:21-jre
WORKDIR /srv
COPY --from=backend_artifact backend.jar /srv/backend.jar
ENV PORT=8080 \
    TEST_SUPPORT_ENABLED=true \
    DATABASE_URL="jdbc:h2:mem:mtf;DB_CLOSE_DELAY=-1"
EXPOSE 8080
USER 10001
HEALTHCHECK --interval=3s --timeout=3s --retries=40 CMD ["bash", "-c", "exec 3<>/dev/tcp/127.0.0.1/8080"]
CMD ["java", "-jar", "/srv/backend.jar"]
