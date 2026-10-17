FROM python:3.11
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN apt-get update && apt-get install -y \
    nmap \
    curl \
    git \
    ruby \
    ruby-dev \
    build-essential \
    wget \
    golang \
    libssl-dev \
    libnet-ssleay-perl \
    libio-socket-ssl-perl \
    libcrypt-ssleay-perl \
    && rm -rf /var/lib/apt/lists/*

# Install Gobuster via pre-compiled binary
RUN wget -q https://github.com/OJ/gobuster/releases/download/v3.6.0/gobuster_Linux_x86_64.tar.gz -O /tmp/gobuster.tar.gz && \
    tar -xzf /tmp/gobuster.tar.gz -C /tmp && \
    mv /tmp/gobuster /usr/local/bin/gobuster && \
    chmod +x /usr/local/bin/gobuster && \
    rm -rf /tmp/gobuster*

# Install Nikto via Git with SSL support
RUN git clone https://github.com/sullo/nikto.git /opt/nikto && \
    ln -s /opt/nikto/program/nikto.pl /usr/local/bin/nikto && \
    chmod +x /usr/local/bin/nikto && \
    cpan -T Net::SSLeay IO::Socket::SSL

# Install SQLMap via Git
RUN git clone --depth 1 https://github.com/sqlmapproject/sqlmap.git /opt/sqlmap && \
    ln -s /opt/sqlmap/sqlmap.py /usr/local/bin/sqlmap && \
    chmod +x /usr/local/bin/sqlmap

RUN gem install wpscan

COPY . .

# Create directory for wordlists if it doesn't exist
RUN mkdir -p /usr/share/wordlists/dirb

RUN if [ ! -f /usr/share/wordlists/dirb/common.txt ]; then \
    wget -q https://raw.githubusercontent.com/danielmiessler/SecLists/master/Discovery/Web-Content/common.txt -O /usr/share/wordlists/dirb/common.txt; \
    fi

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
