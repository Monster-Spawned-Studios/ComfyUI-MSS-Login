"""
Copyright © 2026 Monster Spawned Studios
https://monsterspawned.studio/
All Rights Reserved.
"""

import logging
from os import getcwd, makedirs
from os.path import exists
from argparse import ArgumentParser
import subprocess
from time import sleep
from platform import system, machine

parser = ArgumentParser(
    description="Setup the local development environment for the ComfyUI-MSS-Login extension."
)
parser.add_argument(
    "--python",
    type=str,
    default="3.13",
    help="The version of Python to use for the virtual environment.",
)
parser.add_argument(
    "--dev-group",
    type=str,
    default="dev",
    help="The group to use for the development dependencies.",
)
parser.add_argument(
    "--comfyui-group",
    type=str,
    default="comfyui",
    help="The group to use for the ComfyUI dependencies.",
)
parser.add_argument("--debug", action="store_true", help="Enable debug logging.")
parser.add_argument(
    "--install-comfyui",
    action="store_true",
    help="Install ComfyUI  and its dependencies.",
)
parser.add_argument(
    "--comfyui-dir",
    type=str,
    default="ComfyUI",
    help="The directory to install ComfyUI to.",
)
parser.add_argument(
    "--m-series",
    action="store_true",
    help="Install ComfyUI and its dependencies for Apple M-Series.",
)
parser.add_argument(
    "--cuda",
    action="store_true",
    help="Install ComfyUI and its dependencies for NVIDIA CUDA GPUs.",
)
parser.add_argument(
    "--rocm",
    action="store_true",
    help="Install ComfyUI and its dependencies for AMD ROCm GPUs.",
)
parser.add_argument(
    "--cpu",
    action="store_true",
    help="Install ComfyUI and its dependencies for use with a CPU only.",
)
args = parser.parse_args()

logger = logging.getLogger("MSS-Login Setup Tool")

logger.info(f"The application has started and will now begin its task.")

arg_values = {
    "python_version": args.python,
    "dev_group": args.dev_group,
    "comfyui_group": args.comfyui_group,
    "debug_mode": args.debug,
    "install_comfyui": args.install_comfyui,
    "comfyui_dir": args.comfyui_dir,
    "m_series": args.m_series,
    "cuda": args.cuda,
    "rocm": args.rocm,
    "cpu": args.cpu,
}

log_file = f"{getcwd()}/logs/setup_dev.log"
if not exists(f"{getcwd()}/logs"):
    try:
        makedirs(f"{getcwd()}/logs", exist_ok=True)
    except Exception as e:
        logger.error(f"Failed to create the logs directory: {e}")
        logger.error("Please create the logs directory manually and try again.")
        logger.error(f"The logs directory should be located at: {getcwd()}/logs")
        logger.error("This application will now exit.")
        sleep(3)
    exit(1)

if arg_values["debug_mode"]:
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s - %(levelname)s - %(message)s",
        filename=log_file,
        filemode="a",
    )
    logger.debug("Debug logging enabled.")
else:
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s - %(levelname)s - %(message)s",
        filename=log_file,
        filemode="a",
    )

"""
Setup the local development environment for the ComfyUI-MSS-Login extension.

This function will:
- Create a virtual environment for the project, using the `uv venv` command.
- Install the dependencies for the project, using the `uv sync` command.
- Create a local ComfyUI installation, using the `comfy` command (based on
	the current system platform and architecture).

Args:
    python_version: The version of Python to use for the virtual environment.
    dev_group: The group to use for the development dependencies.
    comfyui_group: The group to use for the ComfyUI dependencies.
"""


def setup_dev(
    python_version: str = "3.13", dev_group: str = "dev", comfyui_group: str = "comfyui"
) -> bool:
    logger.info(
        f"Setting up the local development environment for the ComfyUI-MSS-Login extension using Python {python_version}."
    )
    logger.debug(
        f"Creating a virtual environment for the project using Python {python_version}."
    )
    try:
        subprocess.run(["uv", "venv", "--python", f"{python_version}"], check=True)
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to create a virtual environment for the project: {e}")
        return False
    logger.debug(
        f"Installing the dependencies for the project using the {dev_group} group."
    )
    try:
        subprocess.run(["uv", "sync", "--group", f"{dev_group}"], check=True)
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to install the dependencies for the project: {e}")
        return False
    logger.debug(
        f"Creating a local ComfyUI installation using the {comfyui_group} group."
    )
    try:
        subprocess.run(["comfy", "install", "--group", f"{comfyui_group}"], check=True)
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to create a local ComfyUI installation: {e}")
        return False
    logger.info(
        f"Local development environment setup complete for the ComfyUI-MSS-Login extension using Python {python_version}."
    )
    return True


"""I
Install ComfyUI and its dependencies.

Args:
    python_version: The version of Python to use for the virtual environment.
    dev_group: The group to use for the development dependencies.
    comfyui_group: The group to use for the ComfyUI dependencies.
    debug_mode: Whether to enable debug logging.
    verbose: Whether to enable verbose logging.
    install_comfyui: Whether to install ComfyUI and its dependencies.
"""


def install_comfyui(
    python_version: str = "3.13",
    comfyui_group: str = "comfyui",
    comfyui_dir: str = "ComfyUI",
) -> bool:
    logger.info(
        f"Installing ComfyUI and its dependencies using Python version '{python_version}'."
    )
    logger.debug(
        f"Installing ComfyUI and its dependencies using the {comfyui_group} group into the {comfyui_dir} directory."
    )
    try:
        if (system() == "Darwin" and machine() == "arm64") or arg_values.get(
            "m_series", False
        ):  # Apple M-Series
            subprocess.run(
                [
                    "uv",
                    "run",
                    "--group",
                    f"{comfyui_group}",
                    "comfy",
                    f"--workspace={comfyui_dir.replace('\\', '/')}",
                    "install",
                    "--m-series",
                    "--fast-deps",
                ],
                check=True,
            )
        elif (
            (system() == "Linux" and machine() == "x86_64")
            or (system() == "Windows" and machine() == "x86_64")
            or arg_values.get("cuda", False)
        ):  # Linux x86_64 or Windows x86_64 or NVIDIA CUDA
            subprocess.run(
                [
                    "uv",
                    "run",
                    "--group",
                    f"{comfyui_group}",
                    "comfy",
                    f"--workspace={comfyui_dir.replace('\\', '/')}",
                    "install",
                    "--nvidia",
                    "--fast-deps",
                ],
                check=True,
            )
        elif (system() == "Linux" and machine() == "aarch64") or arg_values.get(
            "rocm", False
        ):  # Linux aarch64 or AMD ROCm
            subprocess.run(
                [
                    "uv",
                    "run",
                    "--group",
                    f"{comfyui_group}",
                    "comfy",
                    f"--workspace={comfyui_dir.replace('\\', '/')}",
                    "install",
                    "--amd",
                    "--fast-deps",
                ],
                check=True,
            )
        elif arg_values.get("cpu", False):  # CPU only
            subprocess.run(
                [
                    "uv",
                    "run",
                    "--group",
                    f"{comfyui_group}",
                    "comfy",
                    f"--workspace={comfyui_dir.replace('\\', '/')}",
                    "install",
                    "--cpu",
                    "--fast-deps",
                ],
                check=True,
            )
        else:
            logger.error("No supported platform found for ComfyUI installation.")
            return False
    except subprocess.CalledProcessError as e:
        logger.error(
            f"Failed to install ComfyUI and its dependencies into the {comfyui_dir} directory: {e}"
        )
        return False
    logger.info(
        f"ComfyUI and its dependencies installed successfully into the {comfyui_dir} directory using Python version '{python_version}'."
    )
    return True


"""
Main function to setup the local development environment for the ComfyUI-MSS-Login extension.

Args:
    python_version: The version of Python to use for the virtual environment.
    dev_group: The group to use for the development dependencies.
    comfyui_group: The group to use for the ComfyUI dependencies.
    debug_mode: Whether to enable debug logging.
    verbose: Whether to enable verbose logging.
    install_comfyui: Whether to install ComfyUI and its dependencies.
"""
if __name__ == "__main__":
    try:
        if setup_dev(
            arg_values["python_version"],
            arg_values["dev_group"],
            arg_values["comfyui_group"],
        ):
            logger.info("Local development environment setup complete.")
            if arg_values.get("install_comfyui", False):
                if install_comfyui(
                    arg_values["python_version"],
                    arg_values["comfyui_group"],
                    arg_values["comfyui_dir"],
                ):
                    logger.info("ComfyUI and its dependencies installed successfully.")
                else:
                    logger.error("Failed to install ComfyUI and its dependencies.")
            else:
                logger.info(
                    "ComfyUI and its dependencies not installed. You can install them manually using the `install_comfyui` flag."
                )
    except Exception as e:
        logger.error(f"Failed to setup the local development environment: {e}")
    finally:
        logger.info("The application has completed its task and will now exit.")
        sleep(3)
        exit(0)
