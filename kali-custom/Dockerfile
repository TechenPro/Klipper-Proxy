# Use the official Kali Linux base image
FROM kalilinux/kali-rolling

# Set environment variables to suppress interactive prompts
ENV DEBIAN_FRONTEND=noninteractive

# Update package lists and install kali-linux-headless
RUN apt update && \
    apt -y full-upgrade && \
    apt -y install kali-linux-headless && \
    apt -y autoremove && \
    apt clean

# Optional: Set a default command to keep the container running
CMD ["/bin/bash"]