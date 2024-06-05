#!/usr/bin/env python
# coding: utf-8

"""
model.py

Handle setup of backbone models, classifiers. Handle model training, optimizers,
learning rate scheduling, tensorboard logging etc.

"""

from argparse import Namespace
import os
import torch
import torch.nn as nn
import torch.optim as optim
import torch.optim.lr_scheduler as lr_scheduler
from torchvision import models

# from torchvision.models.vision_transformer import VisionTransformer
# from torchvision.models import vit_b_16
from typing import Tuple
import numpy as np
import pandas as pd
from torch.utils.data import DataLoader
from sklearn.metrics import roc_auc_score, f1_score, accuracy_score
from tqdm import tqdm
from torch.utils.tensorboard import SummaryWriter
from datetime import datetime
from src.util import generate_model_param_str


# ===================== FullModel combines BackBone and Clf ==========================
class FullModel(nn.Module):
    def __init__(self, backbone, classifier):
        super(FullModel, self).__init__()
        self.backbone = backbone
        self.classifier = classifier

    def forward(self, x):
        x = self.backbone(x)
        x = self.classifier(x)
        return x


# ====================== Backbone model holds pre-trained weights =======================
class Backbone(nn.Module):
    """
    A PyTorch model class serves as the backbone of the pre-trained network by
    removing its classifier.

    Attributes:
        backbone (nn.Sequential): The feature extractor part of the model,
            excluding the original classifier.
        dropout (nn.Dropout): Dropout layer to reduce overfitting.
    """

    def __init__(self, base_model: nn.Module, model_name: str):
        """
        Initializes the BackboneModel class by setting up the modified base
        model and the new classifier.

        Args:
            base_model (nn.Module): The pre-trained base model from which t
                he last layer will be removed.
        """
        super(Backbone, self).__init__()
        self.model_name = model_name
        # Extract the base model without the last layer
        self.backbone = nn.Sequential(*list(base_model.children())[:-1])

    def forward(self, x):
        x = self.backbone(x)
        # DenseNet121 requires additional operations for dimensionality reduction
        if self.model_name == "DenseNet121":
            nn.functional.relu(x, inplace=True)
            x = nn.functional.adaptive_avg_pool2d(x, (1, 1))
        x = torch.flatten(x, 1)
        return x


# ============================ End Backbone Model ============================


# ============================ Define Classifier Models ============================
class LinearClassifier(nn.Module):
    def __init__(self, num_in_features, num_class):
        """
        Initialize a linear classifier layer.

        Input:
            num_in_features (int): input feature size of the last
                (feature extraction) layer from the backbone
            num_class (int): number of classes to be predicted
        """
        super(LinearClassifier, self).__init__()
        self.fc1 = nn.Linear(num_in_features, num_class, bias=True)
        nn.init.kaiming_normal_(self.fc1.weight, nonlinearity="relu")
        nn.init.constant_(self.fc1.bias, 0)

    def forward(self, x):
        return self.fc1(x)


class NonLinearClassifier(nn.Module):
    def __init__(
        self,
        num_in_features: int,
        num_class: int,
        dropout_prob: float = 0.5,
        fc_hidden_size_ratio: float = 0.5,
    ):
        """
        Initialize a nonlinear classifier layer.

        Input:
            num_in_features (int): input feature size of the last
                (feature extraction) layer from the backbone
            dropout_prob (float): probability for element to be zeroed in
                dropout layers
            fc_hidden_size_ratio (float): ratio of FC intermediate layer relative
                to features layer. e.g. 2-> 2 x features = intermediate size
        """
        super(NonLinearClassifier, self).__init__()
        # TODO: maybe play with intermediate sizes
        fc_hidden_size = int(num_in_features * fc_hidden_size_ratio)
        self.relu = nn.LeakyReLU(negative_slope=0.01)

        self.fc1 = nn.Linear(
            num_in_features, fc_hidden_size, bias=False
        )  # dont need bias before a batchnorm, will be cancelled effectively
        self.bn1 = nn.BatchNorm1d(fc_hidden_size)
        self.dropout = nn.Dropout(dropout_prob)  # Adjust dropout rate as needed
        self.fc2 = nn.Linear(fc_hidden_size, num_class, bias=True)

        # Use Kaiming He initialization for better learning (ReLU follows fc1 and fc2)
        nn.init.kaiming_normal_(self.fc1.weight, nonlinearity="relu")
        # no bias to init fc1
        nn.init.kaiming_normal_(self.fc2.weight, nonlinearity="relu")
        nn.init.constant_(self.fc2.bias, 0)

    def forward(self, x):
        x = self.relu(self.bn1(self.fc1(x)))
        x = self.dropout(x)
        x = self.fc2(x)
        return x


class ConvClassifier(nn.Module):
    def __init__(
        self,
        num_in_features: int,
        num_class: int,
        num_filters: int = 4,
        kernel_size: int = 2,
        dropout_prob: float = 0.5,
        fc_hidden_size_ratio: float = 0.5,
    ):
        super(ConvClassifier, self).__init__()

        self.relu = nn.LeakyReLU(negative_slope=0.01)

        self.conv = nn.Conv1d(
            in_channels=1, out_channels=num_filters, kernel_size=kernel_size, bias=False
        )
        self.bn_conv = nn.BatchNorm1d(num_filters)
        self.dropout_conv = nn.Dropout(dropout_prob)

        self.flatten = nn.Flatten()

        conv_output_size = num_in_features - kernel_size + 1
        fc_input_size = num_filters * conv_output_size
        fc_hidden_size = int(fc_input_size * fc_hidden_size_ratio)

        self.fc1 = nn.Linear(fc_input_size, fc_hidden_size, bias=False)
        self.bn_fc1 = nn.BatchNorm1d(fc_hidden_size)
        self.dropout_fc1 = nn.Dropout(dropout_prob)
        self.fc2 = nn.Linear(fc_hidden_size, num_class, bias=True)

        nn.init.kaiming_normal_(self.conv.weight, nonlinearity="relu")
        nn.init.kaiming_normal_(self.fc1.weight, nonlinearity="relu")
        nn.init.kaiming_normal_(self.fc2.weight, nonlinearity="relu")

        if self.conv.bias is not None:
            nn.init.constant_(self.conv.bias, 0)
        if self.fc1.bias is not None:
            nn.init.constant_(self.fc1.bias, 0)
        if self.fc2.bias is not None:
            nn.init.constant_(self.fc2.bias, 0)

    def forward(self, x):
        x = x.unsqueeze(1)
        x = self.relu(self.bn_conv(self.conv(x)))
        x = self.dropout_conv(x)
        x = self.flatten(x)
        x = self.relu(self.bn_fc1(self.fc1(x)))
        x = self.dropout_fc1(x)
        x = self.fc2(x)
        return x


class ConvClassifierWithSkip(nn.Module):
    def __init__(
        self,
        num_in_features: int,
        num_class: int,
        num_filters: int = 4,
        kernel_size: int = 2,
        dropout_prob: float = 0.5,
        fc_hidden_size_ratio: float = 0.5,
    ):
        super(ConvClassifierWithSkip, self).__init__()

        self.relu = nn.LeakyReLU(negative_slope=0.01)

        self.conv = nn.Conv1d(
            in_channels=1, out_channels=num_filters, kernel_size=kernel_size, bias=False
        )
        self.bn_conv = nn.BatchNorm1d(num_filters)
        self.dropout_conv = nn.Dropout(dropout_prob)

        self.flatten = nn.Flatten()

        conv_output_size = num_in_features - kernel_size + 1
        fc_input_size = num_filters * conv_output_size
        fc_hidden_size = int(fc_input_size * fc_hidden_size_ratio)

        self.fc1 = nn.Linear(fc_input_size, fc_hidden_size, bias=False)
        self.bn_fc1 = nn.BatchNorm1d(fc_hidden_size)
        self.dropout_fc1 = nn.Dropout(dropout_prob)
        self.fc2 = nn.Linear(fc_hidden_size, num_class, bias=True)

        # Skip connection
        self.skip = nn.Sequential(
            nn.Linear(num_in_features, fc_input_size, bias=False),
            nn.BatchNorm1d(fc_input_size),
            nn.ReLU(),
        )

        nn.init.kaiming_normal_(self.conv.weight, nonlinearity="relu")
        nn.init.kaiming_normal_(self.fc1.weight, nonlinearity="relu")
        nn.init.kaiming_normal_(self.fc2.weight, nonlinearity="relu")

    def forward(self, x):
        x_initial = x.unsqueeze(1)
        x = self.relu(self.bn_conv(self.conv(x_initial)))
        x = self.dropout_conv(x)
        x = self.flatten(x)

        skip_out = self.skip(x_initial.squeeze(1))
        x = x + skip_out

        x = self.relu(self.bn_fc1(self.fc1(x)))
        x = self.dropout_fc1(x)
        x = self.fc2(x)
        return x


"""Deprecated

class ViTClassifierWithSkip(nn.Module):
    def __init__(
        self,
        num_in_features: int,
        num_classes: int,
        dropout_prob: float = 0.5,
        fc_hidden_size_ratio: float = 0.5,
    ):
        super(ViTClassifierWithSkip, self).__init__()

        self.image_size = int(num_in_features ** 0.5)
        patch_size = 16  # Default patch size for vit_b_16

        self.vit = vit_b_16(weights='IMAGENET1K_V1')

        vit_output_size = self.vit.heads.head.in_features

        fc_hidden_size = int(vit_output_size * fc_hidden_size_ratio)

        self.fc1 = nn.Linear(vit_output_size, fc_hidden_size, bias=False)
        self.bn_fc1 = nn.BatchNorm1d(fc_hidden_size)
        self.dropout_fc1 = nn.Dropout(dropout_prob)
        self.fc2 = nn.Linear(fc_hidden_size, num_classes, bias=True)

        self.skip = nn.Sequential(
            nn.Linear(num_in_features, vit_output_size, bias=False),
            nn.BatchNorm1d(vit_output_size),
            nn.ReLU(),
        )

        nn.init.kaiming_normal_(self.fc1.weight, nonlinearity="relu")
        nn.init.kaiming_normal_(self.fc2.weight, nonlinearity="relu")

        self.vit.heads.head = nn.Identity()

    def forward(self, x):
        x_initial = x

        # Calculate the new dimensions based on the image size and patch size
        num_patches = (self.image_size // 16) ** 2
        if num_patches * 768 != x_initial.size(1):  # 768 is the feature dimension for vit_b_16
            raise ValueError("Input feature size does not match expected size for Vision Transformer.")

        x = x.view(x.size(0), 3, self.image_size, self.image_size)

        x = self.vit(x)

        skip_out = self.skip(x_initial)
        x = x + skip_out

        x = self.fc1(x)
        x = self.bn_fc1(x)
        x = self.dropout_fc1(x)
        x = self.fc2(x)
        return x
"""

# ============================ End Defining Classifiers ============================


# ============================ Model Loading Helpers ============================
def get_compiled_model(
    args: Namespace, device: torch.device
) -> Tuple[nn.Module, optim.Optimizer, nn.CrossEntropyLoss]:
    """
    Prepares and compiles the model by loading a base model, modifying its layers,
    setting the device, and preparing the optimizer and loss function for training.

    Args:
        args (Namespace): Command line arguments or other configuration that
            includes model_name, database, structure, and lr.
        device (torch.device): The device (CPU or GPU) the model should be moved
            to for training.

    Returns:
        tuple:
            - Module: The compiled and ready-to-train model.
            - Optimizer: The optimizer configured for the model.
            - CrossEntropyLoss: The loss function to be used during training.
    """
    # Load the base model + classifier layer
    model = load_model(device, args)

    # Set up the optimizer
    # Original experiments, grid search, freezing etc. used Adam as follows:
    # optimizer = optim.Adam(model.parameters(), lr=args.lr, weight_decay=1e-5)

    # AdamW optimizer: variant of Adam with better handling of weight decay
    # optimizer = optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-1)

    # SGD with Nesterov momentum: often better for image classification tasks
    # Breast best optimizer
    # optimizer = optim.SGD(model.parameters(), lr=args.lr, momentum=0.9, nesterov=True, weight_decay=1e-1)
    optimizer = optim.SGD(
        model.parameters(), lr=args.lr, momentum=0.9, nesterov=True, weight_decay=4e-1
    )  # Acl best optimizer

    # Plain SGD: simple and often effective, without Nesterov momentum
    # optimizer = optim.SGD(model.parameters(), lr=args.lr, weight_decay=1e-4)

    # RAdam (Rectified Adam): combines the benefits of adaptive learning rate and robustness
    # optimizer = optim.RAdam(model.parameters(), lr=args.lr, weight_decay=1e-5)

    # SparseAdam: a variant of Adam meant to handle sparse gradients more efficiently
    # optimizer = optim.SparseAdam(model.parameters(), lr=args.lr)

    # Adamax: a variant of Adam based on the infinity norm, suitable for embeddings and sparse data
    # optimizer = optim.Adamax(model.parameters(), lr=args.lr, weight_decay=1e-4)

    loss = nn.CrossEntropyLoss()

    return model, optimizer, loss


def load_model(device: torch.device, args: Namespace) -> nn.Module:
    """
    Loads a pre-trained model based on the specified model name and database,
    and transfers it to the given device. It supports loading custom weights
    for models trained with the RadImageNet dataset.

    Args:
        backbone_model_name (str): Name of the model to load
            (e.g., 'IRV2', 'ResNet50', 'DenseNet121').
        clf (str): Type of classifier model to use
            (e.g. 'Linear', 'Nonlinear', 'Conv')
        database (str): Indicates the dataset used to pre-train the model
            ('ImageNet' or 'RadImageNet').
        device (torch.device): The device (e.g., CPU or GPU) to which the
            model should be transferred.
        args (Namespace): Command line arguments or other configuration that
            includes model_name, database, structure, and lr.

    Returns:
        Module: The loaded and device-set PyTorch model.

    Raises:
        Exception: If the weights for RadImageNet models do not exist at
            the specified path.
    """
    base_model = None
    model_dir = f"./RadImageNet_pytorch/{args.backbone_model_name}.pt"

    if args.backbone_model_name == "InceptionV3":
        weights = "IMAGENET1K_V1" if args.database == "ImageNet" else None
        base_model = models.inception_v3(
            weights=weights,
            transform_input=False,
            init_weights=False,  # using pretrained weights!!
            aux_logits=weights is not None,
        )  # needs to be set true for imagenet for some reason
        # Remove the auxiliary output layer to allow for
        # smaller input sizes (75x75), otherwise it requires 299x299
        base_model.AuxLogits = None
    elif args.backbone_model_name == "ResNet50":
        weights = "IMAGENET1K_V1" if args.database == "ImageNet" else None
        base_model = models.resnet50(weights=weights)
    elif args.backbone_model_name == "DenseNet121":
        weights = "IMAGENET1K_V1" if args.database == "ImageNet" else None
        base_model = models.densenet121(weights=weights)
    # Determine the number of input features for the classifier
    num_in_features = list(base_model.children())[-1].in_features
    backbone = Backbone(base_model, args.backbone_model_name)

    # Load custom RadImageNet weights if specified and the file exists
    if args.database == "RadImageNet" and os.path.exists(model_dir):
        backbone.load_state_dict(torch.load(model_dir, map_location=device))
    elif args.database == "RadImageNet":
        raise Exception(
            (
                f"RadImageNet model weights for {args.backbone_model_name} do not"
                f" exist at specified path {model_dir}. Please ensure the file exists."
            )
        )

    manage_layer_freezing(backbone, args.structure)

    # define number of output classes depending on task
    if args.data_dir in ["acl", "breast", "hemorrhage", "thyroid"]:
        NUM_CLASS = 2
    if args.clf == "Linear":
        classifier = LinearClassifier(num_in_features, NUM_CLASS)
    elif args.clf == "NonLinear":
        classifier = NonLinearClassifier(
            num_in_features, NUM_CLASS, args.dropout_prob, args.fc_hidden_size_ratio
        )
    elif args.clf == "Conv":
        classifier = ConvClassifier(
            num_in_features, NUM_CLASS, num_filters=args.num_filters
        )
    elif args.clf == "ConvSkip":
        classifier = ConvClassifierWithSkip(
            num_in_features, NUM_CLASS, num_filters=args.num_filters
        )
    elif args.clf == "ConvSkip":
        classifier = ConvClassifierWithSkip(
            num_in_features, NUM_CLASS, num_filters=args.num_filters
        )
    # elif args.clf == "ViT":
    #     classifier = ViTClassifierWithSkip(num_in_features,
    #                                      NUM_CLASS)
    else:
        raise ValueError

    model = FullModel(backbone, classifier)
    model = model.to(device)

    return model


def manage_layer_freezing(model: nn.Module, structure: str) -> None:
    """
    Adjusts the trainable status of layers in a model based on a specified
    structure command. This function can freeze all layers, unfreeze all layers,
    or unfreeze only the top N layers of the model.

    Args:
        model (Module): The PyTorch model whose layer training settings are
            to be modified.
        structure (str): A command string that dictates how layers should be
                        frozen or unfrozen.It can be 'freezeall', 'unfreezeall',
                        or 'unfreezetopN' where N is an integer indicating the
                        number of top layers to unfreeze.

    Raises:
        ValueError: If the structure parameter does not follow the expected
            format or specifies an invalid option.
    """
    children = list(model.children())[0]
    total_layers = len(children)
    # print(f"Total # of layers: {total_layers}")

    if structure == "freezeall":
        # Freeze all layers
        for param in model.parameters():
            param.requires_grad = False

    elif structure == "unfreezeall":
        # Unfreeze all layers
        for param in model.parameters():
            param.requires_grad = True

    elif structure.startswith("unfreezetop"):
        # Attempt to extract the number of layers to unfreeze from the structure string
        try:
            n_layers = int(structure[len("unfreezetop") :])
        except ValueError:
            raise ValueError(
                (
                    "Invalid layer specification. Ensure it follows 'unfreezetopN' "
                    "format where N is a number."
                )
            )

        # Freeze all layers first
        for param in model.parameters():
            param.requires_grad = False

        # Unfreeze the last n_layers
        for i in range(total_layers - n_layers, total_layers):
            # print("\nUnfreezing Layer:\n")
            # print(children[i])
            for param in children[
                i
            ].parameters():  # need to index into the backbone Sequential child (there is only one child)
                param.requires_grad = True

    else:
        raise ValueError(
            (
                "Invalid structure parameter. Use 'freezeall', 'unfreezeall', or "
                "'unfreezetopN' where N is a number."
            )
        )


# ============================ End Model Loading Helpers ============================


# ============================ Run Model Training ============================


def run_model(
    model: nn.Module,
    optimizer: optim.Optimizer,
    loss_fn: nn.Module,
    train_loader: DataLoader,
    val_loader: DataLoader,
    test_loader: DataLoader,
    args: Namespace,
    device: torch.device,
    partial_path: str,
    database: str,
    fold: str,
) -> None:
    num_epochs = args.epoch
    verbose = args.verbose

    MODEL_PARAM_STR = generate_model_param_str(
        data_dir=args.data_dir,
        backbone_model=args.backbone_model_name,
        pretrain=args.database,
        clf=args.clf,
        structure=args.structure,
        lr=args.lr,
        batch_size=args.batch_size,
        dropout_prob=args.dropout_prob,
        fc_hidden_size_ratio=args.fc_hidden_size_ratio,
        num_filters=args.num_filters,
        kernel_size=args.kernel_size,
        epoch=args.epoch,
        image_size=args.image_size,
        lr_decay_method=args.lr_decay_method,
        lr_decay_beta=args.lr_decay_beta,
    )

    save_model_dir = os.path.join(partial_path, "models")
    checkpoint_path = os.path.join(save_model_dir, f"best_model_{MODEL_PARAM_STR}.pth")
    os.makedirs(save_model_dir, exist_ok=True)

    best_val_auc = float("-inf")
    history = {
        "train_loss": [],
        "val_loss": [],
        "train_auc": [],
        "val_auc": [],
        "train_f1": [],
        "val_f1": [],
        "train_accuracy": [],
        "val_accuracy": [],
    }

    current_datetime = datetime.now().strftime("%Y-%m-%d-%H")
    log_dir = os.path.join("logs", f"log_{MODEL_PARAM_STR}_{current_datetime}")
    os.makedirs(log_dir, exist_ok=True)
    writer = SummaryWriter(log_dir=log_dir)

    scheduler = None
    if args.lr_decay_method == "beta":
        gamma = args.lr_decay_beta
        scheduler = lr_scheduler.LambdaLR(
            optimizer, lr_lambda=lambda epoch: gamma**epoch
        )
    elif args.lr_decay_method == "cosine":
        scheduler = lr_scheduler.CosineAnnealingLR(
            optimizer, T_max=args.epoch, eta_min=args.lr / 5
        )

    if args.amp:
        print("\nTurning on Mixed-Precision Training...\n")
        if device.type == "mps":
            print("\nMPS Device Detected! Deactivating AMP (incompatible).\n")
            args.amp = False
    if args.amp:
        gradscaler = torch.cuda.amp.GradScaler()

    for epoch in tqdm(range(num_epochs)):
        model.train()
        running_loss = 0.0
        all_labels = []
        all_scores = []
        all_preds = []
        iter = 0

        for images, labels in train_loader:
            images, labels = images.to(device), labels.to(device)

            optimizer.zero_grad()

            if args.amp:
                with torch.autocast(device_type=device.type):
                    outputs = model(images)
                    loss = loss_fn(outputs, labels)

                gradscaler.scale(loss).backward()
                gradscaler.step(optimizer)
                gradscaler.update()

            else:
                outputs = model(images)
                loss = loss_fn(outputs, labels)
                loss.backward()
                optimizer.step()

            running_loss += loss.item() * images.size(0)

            all_labels.append(labels.cpu().numpy())
            all_scores.append(outputs.detach().cpu().numpy())

            if iter % args.log_every == 0:
                writer.add_scalar(
                    "Loss/train_iter", loss.item(), epoch * len(train_loader) + iter
                )
            iter += 1

        epoch_loss = running_loss / len(train_loader.dataset)
        history["train_loss"].append(epoch_loss)

        all_labels = np.concatenate(all_labels)
        all_scores = np.concatenate(all_scores)
        all_probs = torch.softmax(torch.tensor(all_scores), dim=1).numpy()
        all_preds = np.argmax(all_probs, axis=1)

        # Warning if the model is predicting either all 0s or all 1s in train set
        if np.all(all_preds == 0) or np.all(all_preds == 1):
            print(
                f"Bad Local Minima: Prediction all {np.unique(all_preds)} in train dataset"
            )

        train_auc = roc_auc_score(all_labels, all_probs[:, 1])
        history["train_auc"].append(train_auc)

        train_f1 = f1_score(all_labels, all_preds)
        train_accuracy = accuracy_score(all_labels, all_preds)
        history["train_f1"].append(train_f1)
        history["train_accuracy"].append(train_accuracy)

        if verbose:
            print(
                (
                    f"Epoch {epoch+1}/{num_epochs}, Training Loss: {epoch_loss:.4f}, "
                    f"Training AUC: {train_auc:.4f}, Training F1: {train_f1:.4f}, "
                    f"Training Accuracy: {train_accuracy:.4f}"
                )
            )
        writer.add_scalar("Loss/train", epoch_loss, epoch)
        writer.add_scalar("AUC/train", train_auc, epoch)
        writer.add_scalar("F1/train", train_f1, epoch)
        writer.add_scalar("Accuracy/train", train_accuracy, epoch)

        model.eval()
        val_running_loss = 0.0
        val_labels = []
        val_scores = []

        with torch.no_grad():
            for images, labels in val_loader:
                images, labels = images.to(device), labels.to(device)
                outputs = model(images)
                loss = loss_fn(outputs, labels)
                val_running_loss += loss.item() * images.size(0)

                val_labels.append(labels.cpu().numpy())
                val_scores.append(outputs.cpu().numpy())

        val_loss = val_running_loss / len(val_loader.dataset)
        history["val_loss"].append(val_loss)

        val_labels = np.concatenate(val_labels)
        val_scores = np.concatenate(val_scores)
        val_probs = torch.softmax(torch.tensor(val_scores), dim=1).numpy()
        val_preds = np.argmax(val_probs, axis=1)

        # Warning if the model is predicting either all 0s or all 1s in val set
        if np.all(val_preds == 0) or np.all(val_preds == 1):
            print(
                f"Bad Local Minima: Prediction all {np.unique(val_preds)} in val dataset"
            )

        val_auc = roc_auc_score(val_labels, val_probs[:, 1])
        history["val_auc"].append(val_auc)

        val_f1 = f1_score(val_labels, val_preds)
        val_accuracy = accuracy_score(val_labels, val_preds)
        history["val_f1"].append(val_f1)
        history["val_accuracy"].append(val_accuracy)

        if verbose:
            print(
                (
                    f"Epoch {epoch+1}/{num_epochs}, Validation Loss: {val_loss:.4f}, "
                    f"Validation AUC: {val_auc:.4f}, Validation F1: {val_f1:.4f}, "
                    f"Validation Accuracy: {val_accuracy:.4f}"
                )
            )
        writer.add_scalar("Loss/val", val_loss, epoch)
        writer.add_scalar("AUC/val", val_auc, epoch)
        writer.add_scalar("F1/val", val_f1, epoch)
        writer.add_scalar("Accuracy/val", val_accuracy, epoch)

        if val_auc > best_val_auc:
            best_val_auc = val_auc
            torch.save(
                {
                    "model_state_dict": model.state_dict(),
                    "optimizer_state_dict": optimizer.state_dict(),
                    "epoch": epoch,
                    "best_val_auc": best_val_auc,
                    "args": vars(args),
                },
                checkpoint_path,
            )
            if verbose:
                print(
                    f"Saved model with validation AUC: {val_auc:.4f} at epoch {epoch+1}"
                )

        lr = None
        if scheduler:
            scheduler.step()
            lr = scheduler.get_last_lr()[0]
        else:
            lr = optimizer.param_groups[0]["lr"]
        writer.add_scalar("Learning Rate", lr, epoch)

    checkpoint = torch.load(checkpoint_path)
    model.load_state_dict(checkpoint["model_state_dict"])
    model.eval()
    test_running_loss = 0.0
    test_labels = []
    test_scores = []

    with torch.no_grad():
        for images, labels in test_loader:
            images, labels = images.to(device), labels.to(device)
            outputs = model(images)
            loss = loss_fn(outputs, labels)
            test_running_loss += loss.item() * images.size(0)

            test_labels.append(labels.cpu().numpy())
            test_scores.append(outputs.cpu().numpy())

    test_loss = test_running_loss / len(test_loader.dataset)
    test_labels = np.concatenate(test_labels)
    test_scores = np.concatenate(test_scores)
    test_probs = torch.softmax(torch.tensor(test_scores), dim=1).numpy()
    test_preds = np.argmax(test_probs, axis=1)

    # Warning if the model is predicting either all 0s or all 1s in test set
    if np.all(test_preds == 0) or np.all(test_preds == 1):
        print(
            f"Bad Local Minima: Prediction all {np.unique(test_preds)} in test dataset"
        )

    test_auc = roc_auc_score(test_labels, test_probs[:, 1])
    test_f1 = f1_score(test_labels, test_preds)
    test_accuracy = accuracy_score(test_labels, test_preds)

    history["test_loss"] = test_loss
    history["test_auc"] = test_auc
    history["test_f1"] = test_f1
    history["test_accuracy"] = test_accuracy

    if verbose:
        print(
            (
                f"Test Loss: {test_loss:.4f}, Test AUC: {test_auc:.4f}, "
                f"Test F1: {test_f1:.4f}, Test Accuracy: {test_accuracy:.4f}"
            )
        )

    history_df = pd.DataFrame(history)
    history_df.to_csv(
        os.path.join(save_model_dir, f"training_history_{MODEL_PARAM_STR}.csv"),
        index=False,
    )

    writer.close()
