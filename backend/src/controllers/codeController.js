const analysisService = require("../services/analysisService");

exports.analyzeCode = async (req, res) => {
  try {
    const { code, language } = req.body;
    if (!code) {
      return res.status(400).json({ error: "Code is required" });
    }
    const analysisReport = await analysisService.analyzeCode(code, language);
    res.json({ analysis: analysisReport });
  } catch (error) {
    console.error("Error analyzing code:", error);
    
    // Handle specific error cases
    if (error.message.includes('too large')) {
      return res.status(413).json({ 
        error: "Code is too large for analysis", 
        details: error.message 
      });
    }
    
    res.status(500).json({ error: "Failed to analyze code" });
  }
};

exports.getQueryAboutCode = async (req, res) => {
  try {
    const { code, question } = req.body;

    if (!Array.isArray(code) || code.length === 0) {
      return res
        .status(400)
        .json({ error: "Code files are required in an array." });
    }

    // Set a timeout for the response
    const timeoutId = setTimeout(() => {
      if (!res.headersSent) {
        res.status(408).json({ 
          error: "Request timeout", 
          details: "Analysis took too long. Please try with a more specific query or smaller codebase." 
        });
      }
    }, 45000); // 45 second timeout

    try {
      const analysisReport = await analysisService.getQueryAboutCode(code, question);
      clearTimeout(timeoutId);
      
      if (!res.headersSent) {
        res.json({ response: analysisReport });
      }
    } catch (analysisError) {
      clearTimeout(timeoutId);
      throw analysisError;
    }
  } catch (error) {
    console.error("Error answering code queries:", error);
    
    if (res.headersSent) {
      return; // Response already sent
    }
    
    // Handle specific error cases
    if (error.message.includes('too large')) {
      return res.status(413).json({ 
        error: "Codebase is too large for analysis", 
        details: error.message,
        suggestion: "Try asking about specific files or use more targeted queries like 'explain the authentication system' or 'show me the API routes'"
      });
    }
    
    if (error.message.includes('timeout')) {
      return res.status(408).json({ 
        error: "Analysis request timed out", 
        details: error.message,
        suggestion: "Try with a more specific query or ask about fewer files"
      });
    }
    
    res.status(500).json({ error: "Failed to answer code queries" });
  }
};
