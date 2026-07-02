import argparse
import os
import yaml

from metadata.sdk import configure
from metadata.sdk.entities import TestCases
from metadata.generated.schema.api.tests.createTestCase import CreateTestCaseRequest


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create OpenMetadata test cases from a YAML file.")
    parser.add_argument(
        "yaml_file",
        nargs="?",
        default="dq_tests.yaml",
        help="Path to the YAML file containing test case definitions. Defaults to dq_tests.yaml.",
    )
    return parser.parse_args(argv)


def entity_link(table_fqn: str, column: str | None = None) -> str:
    if column:
        return f"<#E::table::{table_fqn}::columns::{column}>"
    return f"<#E::table::{table_fqn}>"


def configure_openmetadata() -> None:
    host = os.environ.get("OMD_HOST")
    jwt_token = os.environ.get("OMD_JWT_TOKEN")
    if not host or not jwt_token:
        raise RuntimeError("OMD_HOST and OMD_JWT_TOKEN environment variables must be set.")

    configure(host=host, jwt_token=jwt_token)


def load_spec(yaml_file: str) -> dict:
    with open(yaml_file, "r") as f:
        return yaml.safe_load(f)


def create_test_cases(spec: dict) -> None:
    for t in spec["tests"]:
        request = CreateTestCaseRequest(
            name=t["name"],
            testDefinition=t["testDefinition"],
            entityLink=entity_link(t["tableFQN"], t.get("column")),
            parameterValues=t.get("parameters", []),
            description=t.get("description"),
        )

        test_case = TestCases.create(request)
        print(f"Created: {test_case.fullyQualifiedName}")


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    configure_openmetadata()
    create_test_cases(load_spec(args.yaml_file))


if __name__ == "__main__":
    main()
