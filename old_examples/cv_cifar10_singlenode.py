# Databricks notebook source
# MAGIC %pip install -r ../requirements.txt

# COMMAND ----------


import pytorch_lightning as pl

import torch
from torch import nn
from torch.nn import functional as f
from torch.utils.data import DataLoader

from torchmetrics import Accuracy

from torchvision import transforms

from deltatorch.deltadataset import DeltaIterableDataset

# COMMAND ----------

spark_write_path = "/tmp/msh/datasets/cifar"
train_read_path = "/tmp/msh/datasets/cifar"
if locals().get("spark") is not None:
    train_read_path = f"/dbfs{train_read_path}"

# COMMAND ----------


class CIFAR10DataModule(pl.LightningDataModule):
    def __init__(self):
        super().__init__()

        self.transform = transforms.Compose(
            [
                transforms.ToPILImage(),
                transforms.ToTensor(),
                transforms.Normalize((0.5, 0.5, 0.5), (0.5, 0.5, 0.5)),
            ]
        )

        self.num_classes = 10

    # @staticmethod
    # def transform_fn(bytes, transform):
    #     img = Image.fromarray(np.frombuffer(bytes, dtype=np.uint8).reshape((32, 32, 3)))
    #
    #     if transform is not None:
    #         img = transform(img)
    #     return img
    #
    # @staticmethod
    # def to_tuple(x):
    #     return (x["image"], x["label"])

    def dataloader(self, path: str, shuffle=False, batch_size=32, num_workers=0):
        # pipe = DeltaDataPipe(
        #     path,
        #     fields=["image", "label"],
        #     id_field="id",
        #     use_fixed_rank=False,
        #     #fixed_rank=3,
        #     #num_ranks=4,
        # )
        # _transform_fn = partial(self.transform_fn, transform=self.transform)
        # pipe = pipe.map(_transform_fn, input_col="image", output_col="image").map(
        #     self.to_tuple
        # )
        dataset = DeltaIterableDataset(
            path,
            src_field="image",
            target_field="label",
            id_field="id",
            use_fixed_rank=False,
            transform=self.transform,
            apply_src_numpy_shape=(32, 32, 3),
            num_workers=num_workers if num_workers > 0 else 2,
            shuffle=True
            # fixed_rank=3,
            # num_ranks=4,
        )

        return DataLoader(
            dataset, batch_size=batch_size, shuffle=shuffle, num_workers=0
        )

    def train_dataloader(self):
        return self.dataloader(
            f"{train_read_path}_train.delta",
            shuffle=False,
            batch_size=128,
            num_workers=4,
        )

    def val_dataloader(self):
        return self.dataloader(f"{train_read_path}_test.delta")

    def test_dataloader(self):
        return self.dataloader(f"{train_read_path}_test.delta")


class LitModel(pl.LightningModule):
    def __init__(self, input_shape, num_classes, learning_rate=2e-4):
        super().__init__()

        # log hyperparameters
        self.save_hyperparameters()
        self.learning_rate = learning_rate

        self.conv1 = nn.Conv2d(3, 32, 3, 1)
        self.conv2 = nn.Conv2d(32, 32, 3, 1)
        self.conv3 = nn.Conv2d(32, 64, 3, 1)
        self.conv4 = nn.Conv2d(64, 64, 3, 1)

        self.pool1 = torch.nn.MaxPool2d(2)
        self.pool2 = torch.nn.MaxPool2d(2)

        n_sizes = self._get_conv_output(input_shape)

        self.fc1 = nn.Linear(n_sizes, 512)
        self.fc2 = nn.Linear(512, 128)
        self.fc3 = nn.Linear(128, num_classes)

        self.accuracy = Accuracy(task="multiclass", num_classes=10)

    # returns the size of the output tensor going into Linear layer from the conv block.
    def _get_conv_output(self, shape):
        batch_size = 1
        input = torch.autograd.Variable(torch.rand(batch_size, *shape))

        output_feat = self._forward_features(input)
        n_size = output_feat.data.view(batch_size, -1).size(1)
        return n_size

    # returns the feature tensor from the conv block
    def _forward_features(self, x):
        x = f.relu(self.conv1(x))
        x = self.pool1(f.relu(self.conv2(x)))
        x = f.relu(self.conv3(x))
        x = self.pool2(f.relu(self.conv4(x)))
        return x

    # will be used during inference
    def forward(self, x):
        x = self._forward_features(x)
        x = x.view(x.size(0), -1)
        x = f.relu(self.fc1(x))
        x = f.relu(self.fc2(x))
        x = f.log_softmax(self.fc3(x), dim=1)

        return x

    def training_step(self, batch, batch_idx):
        x, y = batch
        logits = self(x)
        loss = f.nll_loss(logits, y)

        # training metrics
        preds = torch.argmax(logits, dim=1)
        acc = self.accuracy(preds, y)
        self.log("train_loss", loss, on_step=True, on_epoch=True, logger=True)
        self.log("train_acc", acc, on_step=True, on_epoch=True, logger=True)

        return loss

    def validation_step(self, batch, batch_idx):
        x, y = batch
        logits = self(x)
        loss = f.nll_loss(logits, y)

        # validation metrics
        preds = torch.argmax(logits, dim=1)
        acc = self.accuracy(preds, y)
        self.log("val_loss", loss, prog_bar=True)
        self.log("val_acc", acc, prog_bar=True)
        return loss

    def test_step(self, batch, batch_idx):
        x, y = batch
        logits = self(x)
        loss = f.nll_loss(logits, y)

        # validation metrics
        preds = torch.argmax(logits, dim=1)
        acc = self.accuracy(preds, y)
        self.log("test_loss", loss, prog_bar=True)
        self.log("test_acc", acc, prog_bar=True)
        return loss

    def configure_optimizers(self):
        optimizer = torch.optim.Adam(self.parameters(), lr=self.learning_rate)
        return optimizer


if __name__ == "__main__":
    dm = CIFAR10DataModule()
    # x = next(iter(dm.train_dataloader()))
    # print(x)

    # Samples required by the custom ImagePredictionLogger callback to log image predictions.
    # val_samples = next(iter(dm.val_dataloader()))
    # val_imgs, val_labels = val_samples[0], val_samples[1]
    # val_imgs.shape, val_labels.shape

    model = LitModel((3, 32, 32), dm.num_classes)

    # Initialize wandb logger

    # Initialize Callbacks
    early_stop_callback = pl.callbacks.EarlyStopping(monitor="val_loss")
    checkpoint_callback = pl.callbacks.ModelCheckpoint()

    # Initialize a trainer
    trainer = pl.Trainer(
        accelerator="gpu",
        max_epochs=5,
        # gpus=0,
        # callbacks=[early_stop_callback, checkpoint_callback],
    )

    # Train the model ⚡🚅⚡
    trainer.fit(model, dm)

    # Evaluate the model on the held-out test set ⚡⚡
    trainer.test(dataloaders=dm.test_dataloader())
