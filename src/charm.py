#!/usr/bin/env python3
"""Asciinema server charm."""

import logging
import typing
from pathlib import Path

import ops
from charmlibs import snap
from charms.data_platform_libs.v0.data_interfaces import (
    DatabaseRequires,
)
from charms.traefik_k8s.v2.ingress import IngressPerAppRequirer

ASCIINEMA_SERVER_SERVICE_FILE = Path("/etc/systemd/system/asciinema_server.service")
DATABASE_RELATION = "database"
ASCIINEMA_DATA_DIR = Path("/home/asciinema")
logger = logging.getLogger()


class AsciinemaCharm(ops.CharmBase):
    """Charm asciinema-server."""

    def __init__(self, *args: typing.Any):
        """Initialize the charm and register event handlers.

        Args:
            args: Arguments to initialize the charm base.
        """
        super().__init__(*args)
        self.database = DatabaseRequires(
            self,
            relation_name=DATABASE_RELATION,
            database_name=self.app.name,
            extra_user_roles="SUPERUSER",
        )
        self.admin_ingress = IngressPerAppRequirer(
            self,
            relation_name="admin",
            port=4002,
        )
        self.framework.observe(self.database.on.database_created, self._reconcile)
        self.framework.observe(self.database.on.endpoints_changed, self._reconcile)
        self.framework.observe(self.server_ingress.on.ready, self._reconcile)
        self.framework.observe(self.server_ingress.on.removed, self._reconcile)

        self.framework.observe(self.on.config_changed, self._reconcile)

    def _reconcile(self, _: typing.Any) -> None:
        """Restart the service."""
        if not self.model.get_relation(DATABASE_RELATION):
            self.unit.status = ops.BlockedStatus("Waiting for DB.")
            return

        self._update_server_configuration()

        self.unit.status = ops.ActiveStatus()

    def _update_server_configuration(self) -> None:
        self.unit.status = ops.MaintenanceStatus("Configuring the asciinema-server snap.")
        server = snap.ensure("asciinema-server", state="present", channel="latest/stable")
        server.connect("home")
        ASCIINEMA_DATA_DIR.mkdir(exist_ok=True, mode=755)

        database_url = None
        if relation := self.model.get_relation(DATABASE_RELATION):
            relation_data = self.database.fetch_relation_data()[relation.id]
            endpoint = relation_data.get("endpoints")
            if endpoint is not None:
                user = relation_data.get("username")
                password = relation_data.get("password")
                host = endpoint.split(":")[0]
                port = endpoint.split(":")[1]
                database_url = f"postgresql://{user}:{password}@{host}:{port}/{self.app.name}"
        host_url = None
        host_port = None
        if proxied_endpoints := self.server_ingress.get_proxied_endpoints():
            host_url = proxied_endpoints[0]
            host_port = 443
        else:
            network_binding = self.model.get_binding("juju-info")
            if (
                network_binding is not None
                and (bind_address := network_binding.network.bind_address) is not None
            ):
                host_url = str(bind_address)
        server.set(
            config={"database.url": database_url, "host.url": host_url, "host.port": host_port}
        )
        server.start()


if __name__ == "__main__":  # pragma: nocover
    ops.main(AsciinemaCharm)
