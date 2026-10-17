const axios = require('axios');
const fs = require('fs-extra');
const path = require('path');

// FastAPI service URL
const FASTAPI_URL = process.env.DJANGO_API_URL || 'http://fastapi-backend:8000';

/**
 * Create embeddings for repository files in FastAPI
 */
const createEmbeddings = async (repoId, files) => {
  try {
    console.log(`🤖 Proxying createEmbeddings to FastAPI for repo: ${repoId}`);
    const response = await axios.post(`${FASTAPI_URL}/api/chatbot/embeddings`, {
      repoId: repoId,
      files: files.map(file => ({
        path: file.path,
        content: file.content || '',
        size: file.size || (file.content ? file.content.length : 0)
      }))
    });
    return response.data.data || response.data;
  } catch (error) {
    console.error('Error creating embeddings in FastAPI:', error.message);
    throw new Error(`Failed to create embeddings: ${error.response?.data?.detail || error.message}`);
  }
};

/**
 * Load embeddings status from FastAPI
 */
const loadEmbeddings = async (repoId) => {
  try {
    console.log(`🤖 Proxying loadEmbeddings status to FastAPI for repo: ${repoId}`);
    const response = await axios.get(`${FASTAPI_URL}/api/chatbot/embeddings/${encodeURIComponent(repoId)}`);
    if (response.data && response.data.exists) {
      // Map it back to a dummy truthy object format that chatbotController expects
      // ChatbotController counts chunks and files from this response
      const filePaths = response.data.files || [];
      const dummyEmbeddings = {};
      filePaths.forEach(p => {
        dummyEmbeddings[p] = { path: p, chunks: [{ summary: { content: '' } }], totalChunks: 1 };
      });
      return dummyEmbeddings;
    }
    return null;
  } catch (error) {
    console.error('Error loading embeddings status from FastAPI:', error.message);
    return null;
  }
};

/**
 * Forward chatbot questions to FastAPI
 */
const chatWithRepository = async (repoId, query, files = null, provider = 'groq') => {
  try {
    console.log(`🤖 Proxying chatWithRepository to FastAPI for repo: ${repoId}`);
    
    // First verify if embeddings exist, if not create them if files are passed
    const statusResponse = await axios.get(`${FASTAPI_URL}/api/chatbot/embeddings/${encodeURIComponent(repoId)}`);
    if (!statusResponse.data.exists && files) {
      console.log(`⚠️ Embeddings missing on FastAPI, creating them first...`);
      const processedFiles = processLargeRepository(files);
      await createEmbeddings(repoId, processedFiles);
    }
    
    const response = await axios.post(`${FASTAPI_URL}/api/chatbot/chat`, {
      repoId,
      query,
      provider
    });
    
    return response.data;
  } catch (error) {
    console.error('Error in chatbot with FastAPI:', error.message);
    throw new Error(error.response?.data?.detail || error.message);
  }
};

/**
 * Forward code analysis task to FastAPI
 */
const analyzeCode = async (query, relevantFiles, provider = 'groq') => {
  try {
    console.log(`🤖 Proxying analyzeCode to FastAPI`);
    const response = await axios.post(`${FASTAPI_URL}/api/chatbot/analyze`, {
      files: relevantFiles.map(f => ({
        path: f.path,
        content: f.content
      })),
      query,
      provider
    });
    return response.data.analysis || response.data;
  } catch (error) {
    console.error('Error analyzing code in FastAPI:', error.message);
    throw new Error(`Failed to analyze code: ${error.response?.data?.detail || error.message}`);
  }
};

/**
 * Filter files for ingestion
 */
const processLargeRepository = (files, maxFiles = 100, maxFileSize = 100000) => {
  const allowedExtensions = ['.js', '.ts', '.py', '.java', '.php', '.rb', '.go', '.cs', '.cpp', '.c', '.jsx', '.tsx', '.vue', '.swift', '.kt', '.html', '.css', '.scss', '.json', '.xml', '.yaml', '.yml'];
  
  return files.filter(file => {
    const ext = path.extname(file.path || '').toLowerCase();
    const size = file.size || (file.content ? file.content.length : 0);
    return allowedExtensions.includes(ext) && 
           size <= maxFileSize &&
           !file.path?.includes('node_modules') &&
           !file.path?.includes('.git') &&
           !file.path?.includes('dist') &&
           !file.path?.includes('build') &&
           !file.path?.includes('coverage');
  }).slice(0, maxFiles);
};

// Placeholder cache functions to match previous exports
const getAnalysisCache = async (repoId) => {
  const cachePath = path.join(__dirname, '../../data/analysis', `${repoId}.json`);
  if (await fs.pathExists(cachePath)) {
    const data = await fs.readJson(cachePath);
    return data.analysis;
  }
  return null;
};

const saveAnalysisCache = async (repoId, analysis) => {
  try {
    const cachePath = path.join(__dirname, '../../data/analysis', `${repoId}.json`);
    await fs.ensureDir(path.dirname(cachePath));
    await fs.writeJson(cachePath, {
      repoId,
      timestamp: new Date().toISOString(),
      analysis
    });
  } catch (error) {
    console.error('Error saving analysis cache:', error);
  }
};

module.exports = {
  createEmbeddings,
  loadEmbeddings,
  chatWithRepository,
  processLargeRepository,
  getAnalysisCache,
  saveAnalysisCache,
  analyzeCode
};