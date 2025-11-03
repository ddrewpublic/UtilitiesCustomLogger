## About

* Rich + file-based logging setup utility for CLI-based data processing workflows.
* Supports config-driven base paths and multiple output files.
* Supports logging terminal output / exceptions to file
* Tested on `Python 3.10.15` 


## Installation
```bash
echo "utilities-custom-logger @ git+ssh://git@github.com/ddrewpublic/utilitiescustomlogger.git@v0.1.0" | tee -a requirements.txt
```


# Usage
```python
from pathlib import Path
from utilities_custom_logger import setup_logger

logger = setup_logger(Path("/path/to/logfile.log").resolve(), level="DEBUG", width=240, exceptions=True)
```