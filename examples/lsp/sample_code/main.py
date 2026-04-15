"""Main entry point for the sample project.

This module demonstrates the LSP Tool capabilities by using various
LSP operations to navigate and understand the codebase.
"""

import sys
from pathlib import Path

# Add project root to path for imports
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

# Import project modules
from models import DataModel, ContainerModel, DataStatus, create_sample_model
from services.calculator import Calculator, get_default_calculator


def main() -> None:
    """Main function demonstrating project structure."""
    print("Sample Project - LSP Tool Demo")
    print("=" * 50)

    # Create sample data models
    model1 = create_sample_model("First Model", 42)
    model2 = create_sample_model("Second Model", 100)

    print(f"Created model: {model1.name} (id={model1.id})")
    print(f"Created model: {model2.name} (id={model2.id})")

    # Use Calculator
    calc = get_default_calculator()
    result = calc.add(10, 20)
    print(f"Calculator: 10 + 20 = {result}")

    # Container operations
    container = ContainerModel(capacity=50)
    container.add_model(model1)
    container.add_model(model2)
    print(f"Container has {len(container.models)} models")

    print("\nProject loaded successfully!")


if __name__ == "__main__":
    main()
