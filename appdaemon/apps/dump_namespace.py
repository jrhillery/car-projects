
from appdaemon import Hass

class DumpNamespace(Hass):

    def initialize(self):
        for namespace in self.args.get("namespaces", ["default"]):
            self.log(f"=== Namespace dump: {namespace} ===")
            if not self.namespace_exists(namespace):
                self.add_namespace(namespace)
            data = self.get_state(namespace=namespace)
            assert isinstance(data, dict)
            for k, v in data.items():
                self.log(f"  {k}: {v}")
        self.log("=== End dump ===")
