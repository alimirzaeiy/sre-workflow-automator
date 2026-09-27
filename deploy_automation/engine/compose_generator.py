import re
from typing import Optional, Any


class DockerComposeGenerator:
    """
    Generates tailored docker-compose.yml configurations based on project specifications,
    languages, logging needs, port bindings, and environment variables.
    """

    @staticmethod
    def extract_container_port_from_dockerfile(dockerfile_content: str, default_port: int = 8080) -> int:
        """
        Parses Dockerfile for EXPOSE directive.
        """
        if not dockerfile_content:
            return default_port
        matches = re.findall(r"^\s*EXPOSE\s+(\d+)", dockerfile_content, re.MULTILINE | re.IGNORECASE)
        if matches:
            return int(matches[0])
        return default_port

    @staticmethod
    def generate_compose(
        project_name: str,
        registry_image: str,
        container_port: int,
        bound_ip: Optional[str] = None,
        external_port: Optional[int] = None,
        has_public_route: bool = False,
        has_log_shipping: bool = False,
        logging_config: Optional[dict[str, Any]] = None,
    ) -> str:
        """
        Builds a standard docker-compose.yml YAML string.
        """
    @staticmethod
    def generate_service_block(
        project_name: str,
        registry_image: str,
        container_port: int,
        bound_ip: Optional[str] = None,
        external_port: Optional[int] = None,
        has_public_route: bool = False,
        has_log_shipping: bool = False,
        logging_config: Optional[dict[str, Any]] = None,
        target_network: Optional[str] = None,
        target_ip: Optional[str] = None,
    ) -> str:
        clean_name = re.sub(r"[^a-zA-Z0-9_-]", "-", project_name).lower()
        tag_var = re.sub(r"[^a-zA-Z0-9_]", "_", project_name).upper() + "_TAG"

        ports_section = ""
        if has_public_route and bound_ip and external_port:
            ports_section = f"""    ports:
      - "{bound_ip}:{external_port}:{container_port}"
"""

        logging_section = ""
        if has_log_shipping:
            driver = "fluentd"
            addr = "localhost:24224"
            if logging_config:
                driver = logging_config.get("driver", "fluentd")
                options = logging_config.get("options", {})
                addr = options.get("fluentd-address", "localhost:24224")

            logging_section = f"""    logging:
      driver: "{driver}"
      options:
        fluentd-address: "{addr}"
        tag: "docker.{{{{.Name}}}}"
        fluentd-async-connect: "true"
"""

        networks_section = ""
        if target_network:
            if target_ip:
                networks_section = f"""    networks:
      {target_network}:
        ipv4_address: {target_ip}
"""
            else:
                networks_section = f"""    networks:
      - {target_network}
"""

        block = f"""  {clean_name}:
    image: {registry_image}:${{{tag_var}:-main}}
    container_name: {clean_name}
    restart: unless-stopped
    env_file:
      - ./.env
{ports_section}{logging_section}{networks_section}"""
        return block

    @staticmethod
    def generate_compose(
        project_name: str,
        registry_image: str,
        container_port: int,
        bound_ip: Optional[str] = None,
        external_port: Optional[int] = None,
        has_public_route: bool = False,
        has_log_shipping: bool = False,
        logging_config: Optional[dict[str, Any]] = None,
    ) -> str:
        """
        Builds a standard docker-compose.yml YAML string.
        """
        service_block = DockerComposeGenerator.generate_service_block(
            project_name, registry_image, container_port, bound_ip, external_port, has_public_route, has_log_shipping, logging_config
        )
        return f"version: '3.8'\n\nservices:\n{service_block}\n"
