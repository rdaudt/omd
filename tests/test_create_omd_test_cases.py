import importlib
import os
import sys
import types
import unittest
from unittest import mock


def install_metadata_stubs():
    metadata = types.ModuleType("metadata")
    sdk = types.ModuleType("metadata.sdk")
    entities = types.ModuleType("metadata.sdk.entities")
    generated = types.ModuleType("metadata.generated")
    schema = types.ModuleType("metadata.generated.schema")
    api = types.ModuleType("metadata.generated.schema.api")
    tests = types.ModuleType("metadata.generated.schema.api.tests")
    create_test_case = types.ModuleType("metadata.generated.schema.api.tests.createTestCase")

    class FakeTestCases:
        @classmethod
        def create(cls, request):
            return types.SimpleNamespace(fullyQualifiedName="created.test.case")

    sdk.configure = lambda **kwargs: None
    entities.TestCases = FakeTestCases
    create_test_case.CreateTestCaseRequest = object

    sys.modules["metadata"] = metadata
    sys.modules["metadata.sdk"] = sdk
    sys.modules["metadata.sdk.entities"] = entities
    sys.modules["metadata.generated"] = generated
    sys.modules["metadata.generated.schema"] = schema
    sys.modules["metadata.generated.schema.api"] = api
    sys.modules["metadata.generated.schema.api.tests"] = tests
    sys.modules["metadata.generated.schema.api.tests.createTestCase"] = create_test_case


class CreateOmdTestCasesCliTest(unittest.TestCase):
    def setUp(self):
        install_metadata_stubs()
        sys.modules.pop("scripts.create_omd_test_cases", None)
        self.module = importlib.import_module("scripts.create_omd_test_cases")

    def test_parse_args_accepts_yaml_filename(self):
        args = self.module.parse_args(["adventureworks_person_person_test_cases.yaml"])

        self.assertEqual(args.yaml_file, "adventureworks_person_person_test_cases.yaml")

    def test_parse_args_defaults_to_existing_dq_tests_filename(self):
        args = self.module.parse_args([])

        self.assertEqual(args.yaml_file, "dq_tests.yaml")

    def test_configure_openmetadata_reads_host_and_token_from_environment(self):
        with mock.patch.dict(
            os.environ,
            {"OMD_HOST": "http://localhost:8585/api", "OMD_JWT_TOKEN": "test-token"},
            clear=True,
        ):
            with mock.patch.object(self.module, "configure") as configure:
                self.module.configure_openmetadata()

        configure.assert_called_once_with(host="http://localhost:8585/api", jwt_token="test-token")

    def test_configure_openmetadata_requires_host_and_token(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "OMD_HOST and OMD_JWT_TOKEN"):
                self.module.configure_openmetadata()

    def test_create_test_cases_does_not_pass_test_suite_to_create_request(self):
        captured_requests = []

        class FakeCreateTestCaseRequest:
            def __init__(self, **kwargs):
                captured_requests.append(kwargs)

        fake_test_case = types.SimpleNamespace(fullyQualifiedName="created.test.case")
        spec = {
            "testSuite": "adventureworks_person_dq",
            "tests": [
                {
                    "name": "person_person_business_entity_id_not_null",
                    "tableFQN": '"Desktop DB".AdventureWorks2019.Person.Person',
                    "column": "BusinessEntityID",
                    "testDefinition": "columnValuesToBeNotNull",
                    "parameters": [],
                }
            ],
        }

        with mock.patch.object(self.module, "CreateTestCaseRequest", FakeCreateTestCaseRequest):
            with mock.patch.object(self.module.TestCases, "create", return_value=fake_test_case):
                self.module.create_test_cases(spec)

        self.assertNotIn("testSuite", captured_requests[0])


if __name__ == "__main__":
    unittest.main()
