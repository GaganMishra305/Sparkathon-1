const axios = require("axios");
const User = require("../models/User");

/**
 * Fetch GitHub repositories of an authenticated user
 * @param {string} userId - MongoDB user ID
 * @returns {Promise<Array>} - List of repositories
 */
const getUserRepos = async (userId) => {
  try {
    const user = await User.findById(userId).select('+accessToken');

    if (!user || !user.accessToken) {
      throw new Error("User not authenticated or missing access token");
    }

    console.log("Fetching repos for user:", userId);

    const response = await axios.get("https://api.github.com/user/repos", {
      headers: {
        Authorization: `Bearer ${user.accessToken}`,
        Accept: "application/vnd.github.v3+json",
        "User-Agent": "SparkSecure-App"
      },
      params: {
        visibility: "all", // ✅ Fetch both public & private repositories
        per_page: 100,
        sort: "updated",
        direction: "desc"
      },
    });

    console.log(`Successfully fetched ${response.data.length} repositories`);
    return response.data;
  } catch (error) {
    console.error("Error fetching repositories:", error.response?.data || error.message);
    if (error.response?.status === 401) {
      throw new Error("GitHub access token is invalid or expired");
    }
    if (error.response?.status === 403) {
      throw new Error("GitHub API rate limit exceeded");
    }
    throw new Error(`Failed to fetch repositories: ${error.message}`);
  }
};

/**
 * Recursively fetch all files from a GitHub repository
 * @param {string} owner - Repository owner (GitHub username)
 * @param {string} repo - Repository name
 * @param {string} path - Path in the repo (default: root)
 * @param {string} accessToken - User's GitHub OAuth token
 * @returns {Promise<Array>} - List of files with content
 */
const fetchRepoContents = async (owner, repo, path = "", userId, state = { count: 0 }) => {
  try {
    const user = await User.findById(userId).select('+accessToken');
    
    if (!user || !user.accessToken) {
      throw new Error("User not authenticated or missing access token");
    }

    const IGNORED_DIRS = ['node_modules', '.git', 'dist', 'build', 'venv', '.venv', 'vendor', 'target', 'data', 'datasets'];
    const ALLOWED_EXTS = ['.js', '.ts', '.py', '.java', '.go', '.rb', '.php', '.cs', '.cpp', '.c', '.jsx', '.tsx', '.vue', '.html', '.css', '.json', '.yaml', '.yml', '.toml', '.xml', '.ipynb'];
    const MAX_FILE_SIZE = 100000; // 100KB limit
    const MAX_FILES = 80;

    const url = `https://api.github.com/repos/${owner}/${repo}/contents/${path}`;
    const response = await axios.get(url, {
      headers: { Authorization: `Bearer ${user.accessToken}` },
      Accept: "application/vnd.github.v3+json",
    });

    let files = [];

    for (const item of response.data) {
      if (state.count >= MAX_FILES) {
        break;
      }

      if (item.type === "file") {
        const ext = '.' + item.name.split('.').pop().toLowerCase();
        
        // Skip non-code extensions or files larger than 100KB
        if (!ALLOWED_EXTS.includes(ext) || item.size > MAX_FILE_SIZE) {
          continue;
        }

        try {
          const fileResponse = await axios.get(item.download_url, {
            responseType: "arraybuffer",
            timeout: 5000,
          });
          const fileBuffer = Buffer.from(fileResponse.data);

          // Detect if it's a binary file
          const isBinary = fileBuffer.includes(0);
          if (isBinary) {
            console.log(`Binary file detected: ${item.path}`);
            continue; // Skip indexing binary files
          }

          files.push({
            path: item.path,
            content: fileBuffer.toString("utf-8"),
            isBinary: false,
          });
          state.count++;
        } catch (fileErr) {
          console.error(`Skipped downloading file ${item.path}:`, fileErr.message);
        }

      } else if (item.type === "dir") {
        const dirName = item.name.toLowerCase();
        if (IGNORED_DIRS.includes(dirName)) {
          continue; // Skip scanning dependencies/build folders
        }

        const subFiles = await fetchRepoContents(
          owner,
          repo,
          item.path,
          userId,
          state
        );
        files = files.concat(subFiles);
      }
    }

    return files;
  } catch (error) {
    console.error(
      "Error fetching repository contents:",
      error.response?.data || error.message
    );
    throw new Error("Failed to fetch repository contents");
  }
};

/**
 * Fetch repository files for public repositories (used for OWASP analysis)
 * @param {string} owner - Repository owner
 * @param {string} repo - Repository name
 * @returns {Promise<Array>} - List of files with content
 */
const getRepositoryFiles = async (owner, repo) => {
  try {
    console.log(`Fetching files for public repository: ${owner}/${repo}`);
    
    // First, try to get repository contents without authentication for public repos
    const url = `https://api.github.com/repos/${owner}/${repo}/contents`;
    
    const response = await axios.get(url, {
      headers: {
        Accept: "application/vnd.github.v3+json",
        "User-Agent": "SparkSecure-App"
      },
      timeout: 10000 // 10 second timeout
    });

    let files = [];
    const maxFiles = 50; // Limit number of files to analyze
    const allowedExtensions = ['.js', '.ts', '.py', '.java', '.php', '.rb', '.go', '.cs', '.cpp', '.c', '.jsx', '.tsx', '.vue', '.swift', '.kt'];
    
    // Process files recursively but limit depth and count
    const processContents = async (contents, currentDepth = 0, maxDepth = 3) => {
      if (currentDepth > maxDepth || files.length >= maxFiles) {
        return;
      }
      
      for (const item of contents) {
        if (files.length >= maxFiles) break;
        
        if (item.type === "file") {
          // Check if file extension is allowed
          const hasAllowedExt = allowedExtensions.some(ext => 
            item.name.toLowerCase().endsWith(ext)
          );
          
          if (hasAllowedExt && item.size < 100000) { // Skip files larger than 100KB
            try {
              const fileResponse = await axios.get(item.download_url, {
                timeout: 5000,
                responseType: "text"
              });
              
              files.push({
                path: item.path,
                content: fileResponse.data,
                size: item.size,
                name: item.name
              });
              
              console.log(`Added file: ${item.path} (${item.size} bytes)`);
            } catch (fileError) {
              console.log(`Skipped file ${item.path}: ${fileError.message}`);
            }
          }
        } else if (item.type === "dir" && currentDepth < maxDepth) {
          try {
            const dirUrl = `https://api.github.com/repos/${owner}/${repo}/contents/${item.path}`;
            const dirResponse = await axios.get(dirUrl, {
              headers: {
                Accept: "application/vnd.github.v3+json",
                "User-Agent": "SparkSecure-App"
              },
              timeout: 5000
            });
            
            await processContents(dirResponse.data, currentDepth + 1, maxDepth);
          } catch (dirError) {
            console.log(`Skipped directory ${item.path}: ${dirError.message}`);
          }
        }
      }
    };
    
    await processContents(response.data);
    
    console.log(`Successfully fetched ${files.length} files for analysis`);
    return files;
    
  } catch (error) {
    console.error('Error fetching repository files:', error.response?.data || error.message);
    
    if (error.response?.status === 404) {
      throw new Error('Repository not found or is private');
    }
    if (error.response?.status === 403) {
      throw new Error('GitHub API rate limit exceeded or repository access denied');
    }
    
    throw new Error(`Failed to fetch repository files: ${error.message}`);
  }
};

module.exports = { fetchRepoContents, getUserRepos, getRepositoryFiles };
