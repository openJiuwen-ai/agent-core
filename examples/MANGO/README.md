# MANGO: Multi-Agent Network Gradient Optimization

<!-- Reinforced Collaboration in Multi-Agent Flow Networks -->
This is the implementation of our paper "[Reinforced Collaboration in Multi-Agent Flow Networks](http://arxiv.org/abs/2605.12943)".

## Datasets

The datasets are included under the folder `\dateset`. `DROP`, `GPQA`, `GSM8K`, `HumanEval`, `MATH`, `MBPP` and `MMLU` 7 datasets are included. The file structure is:

```bash
dataset
└── (Dataset)
    └── test_try.jsonl
    └── test.jsonl
    └── validate_try.jsonl
    └── validate.jsonl
```

## Quick Start

1. Set up the Python environment:

    ```bash
    # Create and activate a Python 3.10.18 virtual environment
    conda create -n <your_env_name> python=3.10.18

    # Install dependencies
    pip install -r requirements.txt
    ```

2. Configure optimization parameters:
    - Use command line arguments or modify default parameters in `main.py`:

    ```bash
    --train_benchmark_selected      # Train dataset type (Default: math)
    --test_benchmark_selected       # Test dataset type (Default: math)
    --model                         # Selected llm type (Default: gpt-4o-mini)
    --data_train_percent            # Percent of train/valid set (Default: 0.8)
    --threshold                     # Threshold (Default: 0.7)
    --concurrency                   # Asynchronous concurrency (Default: 30)
    --num_rl_episodes               # RL episodes (Default: 75)
    --num_tg_episodes               # TextGrad episodes (Default: 5)
    --result_dir                    # Check point save path (Default: /ckpt)
    ```

3. Set LLM parameters in `main.py`:

    ```bash
    os.environ["OPENAI_API_KEY"] = "your_key"
    os.environ["OPENAI_API_BASE"] = "your_url"
    ```

4. Run the training and test:

    ```bash
    # Using default parameters
    python main.py

    # Or using other parameters
    python main.py --train_benchmark_selected humaneval --test_benchmark_selected mbpp --model gpt-4o-mini ...
    ```

## Citation

If you find this repo useful, please consider citing our paper as follows:

```bibtex
@article{wang2026mango,
  title={Reinforced Collaboration in Multi-Agent Flow Networks},
  author={Wang, Zheng and Liu, Yuang and Ding, Yangkai},
  journal={arXiv preprint arXiv:2605.12943},
  year={2026}
}
```

<!-- ## Reproduce the Results in the Paper

1. We provide the raw data obtained from our experiments in the `main_experiments.ipynb` file.

2. You can directly reproduce our experimental results by running main.py with corresponding configs. -->
