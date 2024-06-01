"""
experiment.py

Overall wrapper for running ablation experiments for the medical classification
transfer learning.

Usage:
    $ python experiment.py --method runall         # run all experiments, save results and models
    $ python experiment.py --method summarize      # take all experiments and create overall results csv
    $ python experiment.py --method visualize      # run visualizations
"""
import argparse
import itertools
import os
import sys
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from main import main as run_experiment
from tqdm import tqdm
import seaborn as sns


def summarize_results(results_dirs, verbose=False):
    """
    Summarizes the results from the experiments.

    Args:
        results_dirs (list): List of directories where the results are stored.
        verbose (bool): Whether to print verbose output.

    Returns:
        pd.DataFrame: DataFrame containing the summarized results.
    """
    if verbose:
        print("Summarizing results...")

    summary_rows = []

    for results_dir in results_dirs:
        if not os.path.exists(results_dir):
            if verbose:
                print(f"Directory {results_dir} does not exist.")
            continue

        for filename in os.listdir(results_dir):
            if filename.startswith("training_history_") and filename.endswith(".csv"):
                df = pd.read_csv(os.path.join(results_dir, filename))
                # Extract hyperparameters from the filename
                hyperparams = filename[len("training_history_") : -len(".csv")]
                best_row = df.loc[df["val_auc"].idxmax()].copy()

                # Extract hyperparameters into individual columns
                hyperparam_dict = parse_hyperparams(hyperparams)

                # Add task from parent directory or filename
                task = results_dir.split(os.sep)[-2]
                hyperparam_dict["task"] = task

                best_row = pd.concat([best_row, pd.Series(hyperparam_dict)])

                summary_rows.append(best_row)

    summary_df = pd.DataFrame(summary_rows)
    summary_df.to_csv(os.path.join("results", "results.csv"), index=False)
    if verbose:
        print("Summary saved to results/results.csv")
    return summary_df


def parse_hyperparams(hyperparams_str):
    """
    Parses the hyperparameters from the string.

    Args:
        hyperparams_str (str): The hyperparameters string.

    Returns:
        dict: A dictionary of hyperparameters.
    """
    params = hyperparams_str.split("_")
    hyperparams = {}
    i = 0
    while i < len(params):
        if params[i] == "backbone":
            hyperparams["backbone"] = params[i + 1]
            i += 2
        elif params[i] == "clf":
            hyperparams["clf"] = params[i + 1]
            i += 2
        elif params[i] == "fold":
            hyperparams["fold"] = params[i + 1]
            i += 2
        elif params[i] == "structure":
            hyperparams["structure"] = params[i + 1]
            i += 2
        elif params[i] == "lr":
            hyperparams["lr"] = params[i + 1]
            i += 2
        elif params[i] == "batchsize":
            hyperparams["batchsize"] = params[i + 1]
            i += 2
        elif params[i] == "dropprob":
            hyperparams["dropprob"] = params[i + 1]
            i += 2
        elif params[i] == "fcsizeratio":
            hyperparams["fcsizeratio"] = params[i + 1]
            i += 2
        elif params[i] == "numfilters":
            hyperparams["numfilters"] = params[i + 1]
            i += 2
        elif params[i] == "kernelsize":
            hyperparams["kernelsize"] = params[i + 1]
            i += 2
        elif params[i] == "epochs":
            hyperparams["epochs"] = params[i + 1]
            i += 2
        else:
            i += 1
    return hyperparams


def create_visualizations(summary_df, verbose=False):
    """
    Creates visualizations from the summarized results.

    Args:
        summary_df (pd.DataFrame): DataFrame containing the summarized results.
        verbose (bool): Whether to print verbose output.
    """
    if verbose:
        print("Creating visualizations...")

    tasks = [None, "acl", "breast"]
    task_names = ["overall", "acl", "breast"]
    metrics = [
        "val_auc",
        "val_f1",
        "val_accuracy",
        "test_auc",
        "test_f1",
        "test_accuracy",
    ]
    metric_titles = {
        "val_auc": "Validation AUC",
        "val_f1": "Validation F1",
        "val_accuracy": "Validation Accuracy",
        "test_auc": "Test AUC",
        "test_f1": "Test F1",
        "test_accuracy": "Test Accuracy",
    }
    hyperparameters = [
        "backbone",
        "clf",
        "structure",
        "lr",
        "batchsize",
        "dropprob",
        "fcsizeratio",
        "numfilters",
        "kernelsize",
        "epochs",
    ]

    total_tasks = len(tasks) * len(metrics) * len(hyperparameters)
    with tqdm(total=total_tasks, desc="Creating Visualizations") as pbar:
        for task, task_name in zip(tasks, task_names):
            if task is None:
                task_df = summary_df
            else:
                if "task" not in summary_df.columns:
                    if verbose:
                        print(
                            f"No 'task' column in summary_df. Skipping {task_name} task."
                        )
                    pbar.update(len(metrics) * len(hyperparameters))
                    continue
                task_df = summary_df[summary_df["task"] == task]

            if task_df.empty:
                if verbose:
                    print(f"No data for task: {task_name}")
                pbar.update(len(metrics) * len(hyperparameters))
                continue

            for metric in metrics:
                for hyperparam in hyperparameters:
                    if hyperparam in task_df.columns and metric in task_df.columns:
                        task_df_filtered = task_df.dropna(subset=[hyperparam, metric])
                        if task_df_filtered.empty:
                            pbar.update(1)
                            continue
                        plt.figure(figsize=(10, 6))
                        sns.boxplot(
                            x=hyperparam,
                            y=metric,
                            data=task_df_filtered,
                            palette="Set3",
                            hue=hyperparam,
                            dodge=False,
                            legend=False,
                        )
                        scatter = sns.stripplot(
                            x=hyperparam,
                            y=metric,
                            data=task_df_filtered,
                            jitter=True,
                            dodge=True,
                            marker="o",
                            alpha=0.7,
                            edgecolor="black",
                            linewidth=0.5,
                            hue=hyperparam,
                            palette="Set3",
                            legend=False,
                        )
                        plt.title(
                            f"{task_name.capitalize()} {metric_titles[metric]} by {hyperparam}"
                        )
                        plt.xlabel(hyperparam)
                        plt.ylabel(metric_titles[metric])
                        plt.xticks(rotation=45, ha="right")
                        plt.grid(False)
                        plt.tight_layout()

                        unique_values = task_df_filtered[hyperparam].unique()
                        colors = sns.color_palette("Set3", len(unique_values))
                        custom_handles = [
                            Line2D(
                                [0],
                                [0],
                                marker="o",
                                color="w",
                                label=label,
                                markerfacecolor=color,
                                markersize=10,
                                markeredgecolor="black",
                            )
                            for label, color in zip(unique_values, colors)
                        ]
                        plt.legend(
                            custom_handles,
                            unique_values,
                            title="Experiment Scatter Points",
                        )

                        output_dir = os.path.join(
                            "results",
                            task_name,
                            "val" if "val" in metric else "test",
                            metric.split("_")[1],
                        )
                        os.makedirs(output_dir, exist_ok=True)
                        plt.savefig(
                            os.path.join(
                                output_dir, f"{task_name}_{metric}_by_{hyperparam}.png"
                            )
                        )
                        plt.close()

                        if verbose:
                            print(
                                f"Visualization for {hyperparam} in {task_name} {metric_titles[metric]} saved to {output_dir}/"
                            )
                    pbar.update(1)


def main():
    """
    Main function to handle different methods: runall, summarize, visualize.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--method",
        type=str,
        required=True,
        choices=["runall", "summarize", "visualize"],
        help=(
            "Choose one of: runall, summarize, visualize. runall runs the grid "
            "search of experiments specified in main of experiment.py."
            "summarize combines all hyperparameter experiment results found "
            "and their loss / performance vals at point of best validation "
            "performance, and combines this into results/results.csv. "
            "visualize generates visualizations based on results.csv"
        ),
    )
    parser.add_argument("--verbose", action="store_true", help="Enable verbose output")
    args = parser.parse_args()

    DATA_DIRS = ["breast", "acl"]
    DATABASES = ["ImageNet", "RadImageNet"]
    BACKBONE_MODELS = ["ResNet50", "DenseNet121"]
    CLFS = ["Linear", "NonLinear", "Conv", "ConvSkip"]
    LEARNING_RATES = [1e-4]
    BATCH_SIZES = [128]
    IMAGE_SIZES = [256]
    EPOCHS = [5]
    STRUCTURES = ["freezeall"]
    LR_DECAY_METHODS = ["beta", "cosine"]
    LR_DECAY_BETAS = [0.8]
    DROPOUT_PROBS = [0.5]
    FC_HIDDEN_SIZE_RATIOS = [0.5, 2]
    NUM_FILTERS = [16]  # should really only vary this when CLF is Conv or ConvSkip
    KERNEL_SIZES = [2]
    AMPS = [True]
    USE_FOLDS = [False]
    LOG_EVERY = [100]

    if args.method == "runall":
        if args.verbose:
            print("Running all experiments...")

        for (
            data_dir,
            database,
            backbone_model,
            clf,
            lr,
            batch_size,
            image_size,
            epoch,
            structure,
            lr_decay_method,
            lr_decay_beta,
            dropout_prob,
            fc_hidden_size_ratio,
            num_filters,
            kernel_size,
            amp,
            use_folds,
            log_every,
        ) in tqdm(
            itertools.product(
                DATA_DIRS,
                DATABASES,
                BACKBONE_MODELS,
                CLFS,
                LEARNING_RATES,
                BATCH_SIZES,
                IMAGE_SIZES,
                EPOCHS,
                STRUCTURES,
                LR_DECAY_METHODS,
                LR_DECAY_BETAS,
                DROPOUT_PROBS,
                FC_HIDDEN_SIZE_RATIOS,
                NUM_FILTERS,
                KERNEL_SIZES,
                AMPS,
                USE_FOLDS,
                LOG_EVERY,
            ),
            total=len(DATA_DIRS)
            * len(DATABASES)
            * len(BACKBONE_MODELS)
            * len(CLFS)
            * len(LEARNING_RATES)
            * len(BATCH_SIZES)
            * len(IMAGE_SIZES)
            * len(EPOCHS)
            * len(STRUCTURES)
            * len(LR_DECAY_METHODS)
            * len(LR_DECAY_BETAS)
            * len(DROPOUT_PROBS)
            * len(FC_HIDDEN_SIZE_RATIOS)
            * len(NUM_FILTERS)
            * len(KERNEL_SIZES)
            * len(AMPS)
            * len(USE_FOLDS)
            * len(LOG_EVERY),
            desc="Running experiments",
        ):
            if args.verbose:
                print()
                print(
                    "===================================================================="
                )
                print(
                    f"Running experiment with {data_dir}, {database}, {backbone_model}, {clf}, {lr}, "
                    f"{batch_size}, {image_size}, {epoch}, {structure}, {lr_decay_method}, {lr_decay_beta}, "
                    f"{dropout_prob}, {fc_hidden_size_ratio}, {num_filters}, {kernel_size}, {amp}, {use_folds}, "
                    f"{log_every}"
                )
            # Set sys.argv for the run_experiment call
            sys.argv = (
                [
                    "main.py",
                    "--data_dir",
                    data_dir,
                    "--database",
                    database,
                    "--backbone_model_name",
                    backbone_model,
                    "--clf",
                    clf,
                    "--batch_size",
                    str(batch_size),
                    "--image_size",
                    str(image_size),
                    "--epoch",
                    str(epoch),
                    "--structure",
                    structure,
                    "--lr",
                    str(lr),
                    "--lr_decay_method",
                    lr_decay_method if lr_decay_method is not None else "",
                    "--lr_decay_beta",
                    str(lr_decay_beta),
                    "--dropout_prob",
                    str(dropout_prob),
                    "--fc_hidden_size_ratio",
                    str(fc_hidden_size_ratio),
                    "--num_filters",
                    str(num_filters),
                    "--kernel_size",
                    str(kernel_size),
                    "--log_every",
                    str(log_every),
                ]
                + (["--amp"] if amp else [])
                + (["--use_folds"] if use_folds else [])
                + (["--verbose"] if args.verbose else [])
            )
            run_experiment()

    elif args.method == "summarize":
        results_dirs = [
            os.path.join("data", "acl", "models"),
            os.path.join("data", "breast", "models"),
        ]
        summary_df = summarize_results(results_dirs, args.verbose)

    elif args.method == "visualize":
        results_csv = os.path.join("results", "results.csv")
        if not os.path.exists(results_csv):
            raise FileNotFoundError(
                "The results summary CSV does not exist. Run `summarize` first."
            )
        summary_df = pd.read_csv(results_csv)
        create_visualizations(summary_df, args.verbose)


if __name__ == "__main__":
    main()
