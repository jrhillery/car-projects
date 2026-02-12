
from appdaemon import Hass

class DumpNamespace(Hass):

    def initialize(self):
        namespace = self.args.get("namespace", "default")
        self.log(f"=== Namespace dump: {namespace} ===")
        data = self.get_state(namespace=namespace)
        assert isinstance(data, dict)
        for k, v in data.items():
            self.log(f"  {k}: {v}")
        self.log("=== End dump ===")
