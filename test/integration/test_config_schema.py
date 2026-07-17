from pathlib import Path

from galaxy_test.driver import integration_util
from galaxy.util.yaml_util import ordered_load


class TestConfigSchema(integration_util.IntegrationTestCase):
    def test_schema_path_resolution_graph(self):
        # Run schema's validation method; throws error if schema invalid
        schema = self._app.config.schema
        schema.validate_path_resolution_graph()

    def test_crypt4gh_schema_surface(self):
        schema_path = Path(__file__).resolve().parents[2] / "lib/galaxy/config/schemas/config_schema.yml"
        with schema_path.open() as schema_file:
            schema_document = ordered_load(schema_file)

        galaxy_mapping = schema_document["mapping"]["galaxy"]["mapping"]

        assert "enable_crypt4gh_transparent_input_matching" in galaxy_mapping
        assert "enable_crypt4gh_remote_execution_staging" in galaxy_mapping
        assert "crypt4gh_reencryption_service_url" in galaxy_mapping
        assert "compute-side recryptor B" in galaxy_mapping["crypt4gh_reencryption_service_url"]["desc"]
        assert "crypt4gh_compute_key_path" not in galaxy_mapping
        assert "crypt4gh_compute_key_passphrase_env" not in galaxy_mapping
