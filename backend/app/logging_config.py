import logging


def configure_logging(service: str) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format=(
            f"%(asctime)s %(levelname)s service={service} "
            "logger=%(name)s %(message)s"
        ),
    )
