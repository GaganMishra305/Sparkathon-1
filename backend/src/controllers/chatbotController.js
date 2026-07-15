const geminiService = require('../services/geminiService');
const githubService = require('../services/githubService');
const User = require('../models/User');

/**
 * Chat with repository using AI
 * @route POST /api/chatbot/chat
 */
exports.chatWithRepository = async (req, res) => {
  try {
    const { repoId, query, owner, repo, provider = 'groq' } = req.body;
    
    if (!req.user) {
      return res.status(401).json({ 
        success: false, 
        message: "Unauthorized" 
      });
    }
    
    if (!query || !query.trim()) {
      return res.status(400).json({ 
        success: false, 
        message: "Query is required" 
      });
    }
    
    // Validate provider
    if (!['gemini', 'groq'].includes(provider)) {
      return res.status(400).json({
        success: false,
        message: "Provider must be either 'gemini' or 'groq'"
      });
    }
    
    console.log(`🤖 Chat request for repo ${repoId}: ${query} (using ${provider})`);
    
    let files = null;
    
    // If no repoId provided, try to get files from GitHub
    if (!repoId && owner && repo) {
      try {
        console.log(`📥 Fetching files for ${owner}/${repo}`);
        files = await githubService.getRepositoryFiles(owner, repo);
      } catch (error) {
        console.error('Error fetching repository files:', error);
        return res.status(500).json({
          success: false,
          message: `Failed to fetch repository files: ${error.message}`
        });
      }
    }
    
    // Generate repoId if not provided
    const finalRepoId = repoId || `${owner}/${repo}`;
    
    // Chat with repository using selected provider
    const result = await geminiService.chatWithRepository(finalRepoId, query, files, provider);
    
    res.json({
      success: true,
      data: result
    });
    
  } catch (error) {
    console.error('Error in chatbot chat:', error);
    res.status(500).json({
      success: false,
      message: error.message
    });
  }
};

/**
 * Create embeddings for repository
 * @route POST /api/chatbot/embeddings
 */
exports.createEmbeddings = async (req, res) => {
  try {
    const { repoId, owner, repo } = req.body;
    
    if (!req.user) {
      return res.status(401).json({ 
        success: false, 
        message: "Unauthorized" 
      });
    }
    
    let files;
    let finalRepoId;
    
    if (repoId) {
      finalRepoId = repoId;
      // Try to load existing embeddings first
      const existingEmbeddings = await geminiService.loadEmbeddings(repoId);
      if (existingEmbeddings) {
        return res.json({
          success: true,
          message: "Embeddings already exist",
          data: {
            repoId,
            fileCount: Object.keys(existingEmbeddings).length,
            totalChunks: Object.values(existingEmbeddings).reduce((sum, file) => sum + file.totalChunks, 0)
          }
        });
      }
    } else if (owner && repo) {
      finalRepoId = `${owner}/${repo}`;
      console.log(`📥 Fetching files for ${owner}/${repo}`);
      files = await githubService.getRepositoryFiles(owner, repo);
    } else {
      return res.status(400).json({
        success: false,
        message: "Either repoId or owner/repo combination is required"
      });
    }
    
    if (!files) {
      return res.status(400).json({
        success: false,
        message: "No files found to create embeddings"
      });
    }
    
    console.log(`🔧 Creating embeddings for ${files.length} files`);
    
    // Process large repositories
    const processedFiles = geminiService.processLargeRepository(files);
    
    // Create embeddings
    const embeddings = await geminiService.createEmbeddings(finalRepoId, processedFiles);
    
    res.json({
      success: true,
      message: "Embeddings created successfully",
      data: {
        repoId: finalRepoId,
        fileCount: Object.keys(embeddings).length,
        totalChunks: Object.values(embeddings).reduce((sum, file) => sum + file.totalChunks, 0),
        processedFiles: processedFiles.length
      }
    });
    
  } catch (error) {
    console.error('Error creating embeddings:', error);
    res.status(500).json({
      success: false,
      message: error.message
    });
  }
};

/**
 * Get repository analysis
 * @route GET /api/chatbot/analysis/:repoId
 */
exports.getRepositoryAnalysis = async (req, res) => {
  try {
    const { repoId } = req.params;
    const { provider = 'groq' } = req.query;
    
    if (!req.user) {
      return res.status(401).json({ 
        success: false, 
        message: "Unauthorized" 
      });
    }
    
    // Validate provider
    if (!['gemini', 'groq'].includes(provider)) {
      return res.status(400).json({
        success: false,
        message: "Provider must be either 'gemini' or 'groq'"
      });
    }
    
    // Try to get cached analysis
    const cachedAnalysis = await geminiService.getAnalysisCache(repoId);
    
    if (cachedAnalysis) {
      return res.json({
        success: true,
        data: cachedAnalysis,
        cached: true,
        provider
      });
    }
    
    // Load embeddings
    const embeddings = await geminiService.loadEmbeddings(repoId);
    
    if (!embeddings) {
      return res.status(404).json({
        success: false,
        message: "No embeddings found for this repository"
      });
    }
    
    // Create comprehensive analysis using selected provider
    const analysis = await geminiService.analyzeCode(
      "Provide a comprehensive security and code quality analysis of this repository",
      Object.values(embeddings).map(emb => ({
        path: emb.path,
        content: emb.chunks[0]?.summary.content || ''
      })),
      provider
    );
    
    const analysisData = {
      repoId,
      analysis,
      fileCount: Object.keys(embeddings).length,
      timestamp: new Date().toISOString(),
      provider
    };
    
    // Cache the analysis
    await geminiService.saveAnalysisCache(repoId, analysisData);
    
    res.json({
      success: true,
      data: analysisData,
      cached: false,
      provider
    });
    
  } catch (error) {
    console.error('Error getting repository analysis:', error);
    res.status(500).json({
      success: false,
      message: error.message
    });
  }
};

/**
 * Get embeddings status
 * @route GET /api/chatbot/embeddings/:repoId
 */
exports.getEmbeddingsStatus = async (req, res) => {
  try {
    const { repoId } = req.params;
    
    if (!req.user) {
      return res.status(401).json({ 
        success: false, 
        message: "Unauthorized" 
      });
    }
    
    const embeddings = await geminiService.loadEmbeddings(repoId);
    
    if (embeddings) {
      res.json({
        success: true,
        data: {
          repoId,
          exists: true,
          fileCount: Object.keys(embeddings).length,
          totalChunks: Object.values(embeddings).reduce((sum, file) => sum + file.totalChunks, 0),
          files: Object.keys(embeddings).slice(0, 10) // Show first 10 files
        }
      });
    } else {
      res.json({
        success: true,
        data: {
          repoId,
          exists: false,
          fileCount: 0,
          totalChunks: 0
        }
      });
    }
    
  } catch (error) {
    console.error('Error getting embeddings status:', error);
    res.status(500).json({
      success: false,
      message: error.message
    });
  }
};

/**
 * Delete embeddings
 * @route DELETE /api/chatbot/embeddings/:repoId
 */
exports.deleteEmbeddings = async (req, res) => {
  try {
    const { repoId } = req.params;
    
    if (!req.user) {
      return res.status(401).json({ 
        success: false, 
        message: "Unauthorized" 
      });
    }
    
    const fs = require('fs-extra');
    const path = require('path');
    
    const embeddingsPath = path.join(__dirname, '../../data/embeddings', `${repoId}.json`);
    const analysisPath = path.join(__dirname, '../../data/analysis', `${repoId}.json`);
    
    let deleted = false;
    
    if (await fs.pathExists(embeddingsPath)) {
      await fs.remove(embeddingsPath);
      deleted = true;
    }
    
    if (await fs.pathExists(analysisPath)) {
      await fs.remove(analysisPath);
    }
    
    res.json({
      success: true,
      message: deleted ? "Embeddings deleted successfully" : "No embeddings found to delete",
      data: { repoId, deleted }
    });
    
  } catch (error) {
    console.error('Error deleting embeddings:', error);
    res.status(500).json({
      success: false,
      message: error.message
    });
  }
};

/**
 * Get all embeddings
 * @route GET /api/chatbot/embeddings
 */
exports.getAllEmbeddings = async (req, res) => {
  try {
    if (!req.user) {
      return res.status(401).json({ 
        success: false, 
        message: "Unauthorized" 
      });
    }
    
    const fs = require('fs-extra');
    const path = require('path');
    
    const embeddingsDir = path.join(__dirname, '../../data/embeddings');
    const files = await fs.readdir(embeddingsDir);
    
    const embeddings = [];
    
    for (const file of files) {
      if (file.endsWith('.json')) {
        try {
          const data = await fs.readJson(path.join(embeddingsDir, file));
          embeddings.push({
            repoId: data.repoId,
            fileCount: data.fileCount,
            totalChunks: data.totalChunks || 0,
            createdAt: data.createdAt
          });
        } catch (error) {
          console.error(`Error reading embedding file ${file}:`, error);
        }
      }
    }
    
    res.json({
      success: true,
      data: {
        total: embeddings.length,
        embeddings
      }
    });
    
  } catch (error) {
    console.error('Error getting all embeddings:', error);
    res.status(500).json({
      success: false,
      message: error.message
    });
  }
};

/**
 * Force recreate embeddings
 * @route POST /api/chatbot/embeddings/recreate
 */
exports.recreateEmbeddings = async (req, res) => {
  try {
    const { repoId, owner, repo } = req.body;
    
    if (!req.user) {
      return res.status(401).json({ 
        success: false, 
        message: "Unauthorized" 
      });
    }
    
    let files;
    let finalRepoId;
    
    if (repoId) {
      finalRepoId = repoId;
    } else if (owner && repo) {
      finalRepoId = `${owner}/${repo}`;
    } else {
      return res.status(400).json({
        success: false,
        message: "Either repoId or owner/repo combination is required"
      });
    }
    
    // Delete existing embeddings first
    const fs = require('fs-extra');
    const path = require('path');
    
    const embeddingsPath = path.join(__dirname, '../../data/embeddings', `${finalRepoId}.json`);
    const analysisPath = path.join(__dirname, '../../data/analysis', `${finalRepoId}.json`);
    
    if (await fs.pathExists(embeddingsPath)) {
      await fs.remove(embeddingsPath);
    }
    
    if (await fs.pathExists(analysisPath)) {
      await fs.remove(analysisPath);
    }
    
    // Fetch fresh files
    if (owner && repo) {
      console.log(`📥 Fetching fresh files for ${owner}/${repo}`);
      files = await githubService.getRepositoryFiles(owner, repo);
    } else {
      return res.status(400).json({
        success: false,
        message: "Owner and repo are required for recreation"
      });
    }
    
    if (!files) {
      return res.status(400).json({
        success: false,
        message: "No files found to create embeddings"
      });
    }
    
    console.log(`🔧 Recreating embeddings for ${files.length} files`);
    
    // Process large repositories
    const processedFiles = geminiService.processLargeRepository(files);
    
    // Create embeddings
    const embeddings = await geminiService.createEmbeddings(finalRepoId, processedFiles);
    
    res.json({
      success: true,
      message: "Embeddings recreated successfully",
      data: {
        repoId: finalRepoId,
        fileCount: Object.keys(embeddings).length,
        totalChunks: Object.values(embeddings).reduce((sum, file) => sum + file.totalChunks, 0),
        processedFiles: processedFiles.length
      }
    });
    
  } catch (error) {
    console.error('Error recreating embeddings:', error);
    res.status(500).json({
      success: false,
      message: error.message
    });
  }
}; 