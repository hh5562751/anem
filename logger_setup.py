# logger_setup.py
import logging
import sys
from config import LOG_FILE # Import LOG_FILE from config.py

def setup_logging():
    """Configures and returns a logger instance."""
    # Configure the root logger
    # This setup will apply to all loggers obtained via logging.getLogger()
    # unless they are specifically configured otherwise.
    logging.basicConfig(
        level=logging.INFO, # Set the desired logging level (e.g., INFO, DEBUG)
        format="%(asctime)s - %(levelname)s - %(threadName)s - %(filename)s:%(lineno)d - %(message)s",
        handlers=[
            logging.FileHandler(LOG_FILE, encoding='utf-8'), # Log to a file, ensuring UTF-8 encoding
            logging.StreamHandler(sys.stdout) # Also log to console (standard output)
        ]
    )
    # Get a logger instance (optional if basicConfig is sufficient for all modules)
    # If other modules use logging.getLogger(__name__), they will inherit this config.
    logger = logging.getLogger(__name__) 
    # No need to add handlers to this specific logger if basicConfig handlers are sufficient.
    # If you wanted this logger to have unique handlers, you would add them here.
    return logger

# Example of how this might be used in another file (e.g., main_app.py at the beginning):
# from logger_setup import setup_logging
# logger = setup_logging() # Call this once at the start of your application
# logger.info("Logging is configured.")
