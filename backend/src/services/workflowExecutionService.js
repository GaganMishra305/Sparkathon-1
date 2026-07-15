const Workflow = require("../models/Workflow");
const djangoScanService = require("./djangoScanService");
const notificationService = require("./notificationService");
const { v4: uuidv4 } = require("uuid");

class WorkflowExecutionService {
  constructor() {
    this.executionQueue = new Map();
  }

  /**
   * Execute a workflow
   * @param {Object} workflow - Workflow document from MongoDB
   * @returns {Promise<Object>} Execution result
   */
  async executeWorkflow(workflow) {
    const executionId = uuidv4();

    try {
      console.log(
        `Starting workflow execution: ${workflow.name} (${executionId})`
      );

      // Update workflow status
      workflow.lastExecution = {
        status: "running",
        startedAt: new Date(),
        results: {},
        error: null,
      };
      await workflow.save();

      // Store execution in queue
      this.executionQueue.set(executionId, {
        workflowId: workflow._id,
        status: "running",
        startedAt: new Date(),
      });

      // Execute workflow asynchronously
      this.executeWorkflowSteps(workflow, executionId).catch((error) => {
        console.error(`Workflow execution failed: ${executionId}`, error);
      });

      return { executionId, status: "running" };
    } catch (error) {
      console.error("Failed to start workflow execution:", error);
      throw error;
    }
  }

  /**
   * Execute workflow steps
   * @param {Object} workflow - Workflow document
   * @param {string} executionId - Execution ID
   */
  async executeWorkflowSteps(workflow, executionId) {
    try {
      const { nodes, edges } = workflow;

      // Find trigger node
      const triggerNode = nodes.find((node) => node.type === "trigger");
      if (!triggerNode) {
        throw new Error("No trigger node found in workflow");
      }

      // Get target URL from trigger
      const targetUrl = triggerNode.data?.sourceUrl || triggerNode.data?.url;
      if (!targetUrl) {
        throw new Error("No target URL specified in trigger node");
      }

      console.log(`Executing workflow for target: ${targetUrl}`);

      // Build execution graph
      const executionGraph = this.buildExecutionGraph(nodes, edges);

      // Execute nodes in sequence
      const results = await this.executeNodes(
        executionGraph,
        triggerNode.id,
        targetUrl
      );

      console.log(`Workflow execution completed successfully: ${executionId}`);
      console.log("Results:", JSON.stringify(results, null, 2));

      // AI report is now generated during execution phase, not here

      // Update workflow with success
      workflow.lastExecution = {
        status: "completed",
        startedAt: workflow.lastExecution.startedAt,
        completedAt: new Date(),
        results,
        error: null,
      };
      await workflow.save();

      console.log("Workflow saved with results");

      // Send notifications if configured
      try {
        await this.sendNotifications(workflow, results);
      } catch (notificationError) {
        console.error('Failed to send notifications:', notificationError);
        // Don't fail the workflow for notification errors
      }

      // Remove from execution queue
      this.executionQueue.delete(executionId);

      console.log(`Workflow execution completed: ${executionId}`);
    } catch (error) {
      console.error(`Workflow execution failed: ${executionId}`, error);

      // Update workflow with failure
      workflow.lastExecution = {
        status: "failed",
        startedAt: workflow.lastExecution.startedAt,
        completedAt: new Date(),
        results: {},
        error: error.message,
      };
      await workflow.save();

      // Remove from execution queue
      this.executionQueue.delete(executionId);
    }
  }

  /**
   * Build execution graph from nodes and edges
   * @param {Array} nodes - Workflow nodes
   * @param {Array} edges - Workflow edges
   * @returns {Map} Execution graph
   */
  buildExecutionGraph(nodes, edges) {
    const graph = new Map();

    // Initialize graph with nodes
    nodes.forEach((node) => {
      graph.set(node.id, {
        node,
        children: [],
        parents: [],
      });
    });

    // Add edges to graph
    edges.forEach((edge) => {
      const sourceNode = graph.get(edge.source);
      const targetNode = graph.get(edge.target);

      if (sourceNode && targetNode) {
        sourceNode.children.push(edge.target);
        targetNode.parents.push(edge.source);
      }
    });

    return graph;
  }

  /**
   * Execute nodes in the workflow
   * @param {Map} graph - Execution graph
   * @param {string} startNodeId - Starting node ID
   * @param {string} targetUrl - Target URL
   * @returns {Promise<Object>} Execution results
   */
  async executeNodes(graph, startNodeId, targetUrl) {
    const results = {};
    const executed = new Set();

    // First, identify scan nodes and notification nodes
    const scanNodeTypes = ['nmap', 'gobuster', 'nikto', 'sqlmap', 'wpscan'];
    const notificationNodeTypes = ['email', 'slack', 'github-issue'];
    
    const allNodes = Array.from(graph.keys());
    const scanNodes = allNodes.filter(nodeId => {
      const node = graph.get(nodeId).node;
      return scanNodeTypes.includes(node.type);
    });
    const notificationNodes = allNodes.filter(nodeId => {
      const node = graph.get(nodeId).node;
      return notificationNodeTypes.includes(node.type);
    });
    
    console.log('Execution plan:');
    console.log('- Scan nodes:', scanNodes.length);
    console.log('- Notification nodes:', notificationNodes.length);

    // Phase 1: Execute all scan nodes first
    console.log('Phase 1: Executing scan nodes...');
    await this.executeNodeRecursive(
      graph,
      startNodeId,
      targetUrl,
      results,
      executed,
      null,
      notificationNodeTypes // Skip notification nodes in this phase
    );

    // Phase 2: Generate AI report if we have scan results
    console.log('Phase 2: Generating AI report...');
    if (Object.keys(results).length > 0) {
      try {
        const aiReport = await this.generateAIReport(results, targetUrl);
        if (aiReport.success) {
          console.log('AI report generated successfully for email inclusion');
          results.ai_report = {
            success: true,
            data: aiReport.report,
            timestamp: new Date().toISOString(),
            type: 'ai_report'
          };
        } else {
          console.warn('AI report generation failed');
          results.ai_report = {
            success: false,
            error: aiReport.error,
            timestamp: new Date().toISOString(),
            type: 'ai_report'
          };
        }
      } catch (reportError) {
        console.error('Error generating AI report:', reportError);
        results.ai_report = {
          success: false,
          error: reportError.message,
          timestamp: new Date().toISOString(),
          type: 'ai_report'
        };
      }
    }

    // Phase 3: Execute notification nodes with AI report available
    console.log('Phase 3: Executing notification nodes...');
    const notificationExecuted = new Set();
    for (const nodeId of notificationNodes) {
      if (!executed.has(nodeId)) {
        await this.executeNodeRecursive(
          graph,
          nodeId,
          targetUrl,
          results,
          notificationExecuted,
          results, // Pass all results including AI report
          [] // Don't skip any nodes in this phase
        );
        executed.add(nodeId);
      }
    }

    return results;
  }

  /**
   * Recursively execute nodes
   * @param {Map} graph - Execution graph
   * @param {string} nodeId - Current node ID
   * @param {string} targetUrl - Target URL
   * @param {Object} results - Results accumulator
   * @param {Set} executed - Set of executed node IDs
   * @param {Object} previousResults - Results from previous nodes
   * @param {Array} skipNodeTypes - Node types to skip in this execution phase
   */
  async executeNodeRecursive(
    graph,
    nodeId,
    targetUrl,
    results,
    executed,
    previousResults = null,
    skipNodeTypes = []
  ) {
    if (executed.has(nodeId)) {
      return;
    }

    const graphNode = graph.get(nodeId);
    if (!graphNode) {
      return;
    }

    const { node } = graphNode;
    
    // Skip this node if it's in the skip list
    if (skipNodeTypes.includes(node.type)) {
      console.log(`Skipping ${node.type} node ${nodeId} in current phase`);
      return;
    }
    
    executed.add(nodeId);

    console.log(`Executing node: ${node.type} (${nodeId})`);

    // Execute current node - pass all results for notification nodes
    const nodeResult = await this.executeNode(
      node,
      targetUrl,
      previousResults,
      results
    );
    results[nodeId] = nodeResult;

    // Execute child nodes
    for (const childId of graphNode.children) {
      await this.executeNodeRecursive(
        graph,
        childId,
        targetUrl,
        results,
        executed,
        nodeResult,
        skipNodeTypes
      );
    }
  }

  /**
   * Execute a single node
   * @param {Object} node - Node to execute
   * @param {string} targetUrl - Target URL
   * @param {Object} previousResults - Results from previous nodes
   * @param {Object} allResults - All results accumulated so far
   * @returns {Promise<Object>} Node execution result
   */
  async executeNode(node, targetUrl, previousResults, allResults = {}) {
    try {
      switch (node.type) {
        case "trigger":
          return {
            type: "trigger",
            success: true,
            data: { targetUrl },
            timestamp: new Date().toISOString(),
          };

        case "nmap":
          const nmapArgs = node.data?.scanArgs || "-F";
          return await djangoScanService.nmapScan(targetUrl, nmapArgs);

        case "gobuster":
          return await djangoScanService.gobusterScan(targetUrl);

        case "nikto":
          const niktoArgs = node.data?.scanArgs || "-h";
          return await djangoScanService.niktoScan(targetUrl, niktoArgs);

        case "sqlmap":
          const sqlmapArgs = node.data?.scanArgs || "--batch --random-agent";
          return await djangoScanService.sqlmapScan(targetUrl, sqlmapArgs);

        case "wpscan":
          const wpscanArgs = node.data?.scanArgs || "--random-user-agent";
          return await djangoScanService.wpscanScan(targetUrl, wpscanArgs);

        case "email":
          // Send email notification with all scan results including AI report
          console.log('Processing email node with data:', JSON.stringify(node.data, null, 2));
          
          let recipientEmail = null;
          
          // Try multiple ways to get the email address from node configuration
          if (node.data?.config?.email) {
            recipientEmail = node.data.config.email;
            console.log('Using email from node.data.config.email:', recipientEmail);
          } else if (node.data?.email) {
            recipientEmail = node.data.email;
            console.log('Using email from node.data.email:', recipientEmail);
          } else if (node.data?.to) {
            recipientEmail = node.data.to;
            console.log('Using email from node.data.to:', recipientEmail);
          } else {
            recipientEmail = process.env.DEFAULT_EMAIL_RECIPIENT;
            console.log('Using default email recipient:', recipientEmail);
          }
          
          if (!recipientEmail || recipientEmail === 'your-email@gmail.com') {
            console.error('No valid email recipient found in node configuration');
            return {
              success: false,
              error: 'Email recipient not configured properly. Please set an email address in the email node configuration.',
              timestamp: new Date().toISOString(),
            };
          }
          
          const emailConfig = {
            to: recipientEmail,
            from: process.env.EMAIL_USER
          };
          
          console.log('Final email configuration:', emailConfig);
          
          const subject = `🛡️ VulnPilot Security Report - ${targetUrl}`;

          // Collect all scan results from the workflow
          const allScanResults = Object.values(allResults).filter(
            (r) => r && (r.scanType || r.ai_report)
          );

          console.log('Preparing email with scan results:', allScanResults.length);

          // Check if we have AI report in results
          let emailData = {
            scan_results: allScanResults,
            target_url: targetUrl
          };

          // Look for AI report in allResults
          const aiReportResult = allResults['ai_report'];
          if (aiReportResult && aiReportResult.data) {
            console.log('Including AI report in email');
            emailData.ai_report = aiReportResult.data;
          } else {
            console.log('No AI report found for email');
          }

          if (allScanResults.length === 0 && !emailData.ai_report) {
            console.log('No scan results or AI report to send via email');
            return { 
              success: true, 
              message: "No scan results to send",
              skipped: true 
            };
          }

          const emailResult = await notificationService.sendEmail(
            emailConfig,
            subject,
            emailData
          );

          if (!emailResult.success && !emailResult.skipped) {
            console.error('Email sending failed:', emailResult.error);
          } else if (emailResult.success) {
            console.log('Email sent successfully to:', emailConfig.to);
          }

          return {
            success: emailResult.success || emailResult.skipped,
            data: emailResult,
            timestamp: new Date().toISOString(),
          };
        case "slack":
          // Send Slack notification with all scan results
          const slackConfig = node.data || {};
          const slackTitle = `🔍 Security Scan Alert - ${targetUrl}`;

          // Collect all scan results from the workflow
          const allSlackResults = Object.values(allResults).filter(
            (r) => r && r.scanType
          );

          if (allSlackResults.length === 0) {
            return { success: false, error: "No scan results to send" };
          }

          return await notificationService.sendSlackNotification(
            slackConfig,
            slackTitle,
            allSlackResults
          );

        case "github-issue":
          // Create GitHub issue with all scan results
          const githubConfig = node.data || {};
          const issueTitle = `Security Vulnerability Found - ${targetUrl}`;

          // Collect all scan results from the workflow
          const allGithubResults = Object.values(allResults).filter(
            (r) => r && r.scanType
          );

          if (allGithubResults.length === 0) {
            return { success: false, error: "No scan results to create issue" };
          }

          return await notificationService.createGitHubIssue(
            githubConfig,
            issueTitle,
            allGithubResults
          );

        case 'owasp-vulnerabilities':
          // OWASP vulnerability analysis
          return await this.executeOwaspVulnerabilityAnalysis(targetUrl, node.data, allResults);

        case 'flow-chart':
          // Generate workflow flow chart
          return await this.executeFlowChartGeneration(targetUrl, node.data, allResults);

        default:
          console.warn(`Unknown node type: ${node.type}`);
          return {
            success: false,
            error: `Unknown node type: ${node.type}`,
            type: node.type,
          };
      }
    } catch (error) {
      console.error(`Node execution failed: ${node.type}`, error);
      return {
        success: false,
        error: error.message,
        type: node.type,
        timestamp: new Date().toISOString(),
      };
    }
  }

  /**
   * Get execution status
   * @param {string} executionId - Execution ID
   * @returns {Object|null} Execution status
   */
  getExecutionStatus(executionId) {
    return this.executionQueue.get(executionId) || null;
  }

  /**
   * Send notifications for workflow completion
   * @param {Object} workflow - Workflow document
   * @param {Object} results - Execution results
   */
  async sendNotifications(workflow, results) {
    try {
      const { notifications } = workflow;
      
      if (!notifications || notifications.length === 0) {
        console.log('No notifications configured for this workflow');
        return;
      }

      console.log(`Sending ${notifications.length} notifications...`);

      for (const notification of notifications) {
        try {
          const { type, config } = notification;
          
          switch (type) {
            case 'email':
              await notificationService.sendEmail(
                config,
                `Workflow Execution Complete: ${workflow.name}`,
                results
              );
              break;
              
            case 'slack':
              await notificationService.sendSlackNotification(
                config,
                `Workflow Execution Complete: ${workflow.name}`,
                results
              );
              break;
              
            case 'github':
              await notificationService.createGitHubIssue(
                config,
                `Workflow Execution Results: ${workflow.name}`,
                results
              );
              break;
              
            default:
              console.warn(`Unknown notification type: ${type}`);
          }
        } catch (error) {
          console.error(`Failed to send ${notification.type} notification:`, error);
          // Continue with other notifications
        }
      }
      
      console.log('All notifications sent successfully');
    } catch (error) {
      console.error('Failed to send notifications:', error);
      throw error;
    }
  }

  /**
   * Schedule workflow execution based on frequency
   * @param {Object} workflow - Workflow document
   */
  scheduleWorkflow(workflow) {
    if (!workflow.schedule?.enabled || !workflow.isActive) {
      return;
    }

    const { frequency } = workflow.schedule;
    const intervalMs = this.getIntervalFromFrequency(frequency);

    console.log(`Scheduling workflow: ${workflow.name} every ${frequency}`);

    setInterval(async () => {
      try {
        await this.executeWorkflow(workflow);
      } catch (error) {
        console.error(
          `Scheduled workflow execution failed: ${workflow.name}`,
          error
        );
      }
    }, intervalMs);
  }

  /**
   * Convert frequency string to milliseconds
   * @param {string} frequency - Frequency string
   * @returns {number} Interval in milliseconds
   */
  getIntervalFromFrequency(frequency) {
    const intervals = {
      "2hr": 2 * 60 * 60 * 1000,
      "4hr": 4 * 60 * 60 * 1000,
      "6hr": 6 * 60 * 60 * 1000,
      "12hr": 12 * 60 * 60 * 1000,
      "1 day": 24 * 60 * 60 * 1000,
    };

    return intervals[frequency] || intervals["2hr"];
  }

  /**
   * Execute OWASP vulnerability analysis
   * @param {string} targetUrl - Target URL
   * @param {Object} nodeData - Node configuration data
   * @param {Object} allResults - All scan results so far
   * @returns {Promise<Object>} Analysis result
   */
  async executeOwaspVulnerabilityAnalysis(targetUrl, nodeData = {}, allResults = {}) {
    try {
      console.log(`Starting OWASP vulnerability analysis for: ${targetUrl}`);
      
      // Check if target is a GitHub repository
      if (this.isGitHubRepository(targetUrl)) {
        console.log('Detected GitHub repository, performing code analysis...');
        return await this.performGitHubCodeAnalysis(targetUrl);
      }
      
      // For non-GitHub targets, analyze scan results
      const scanResults = Object.values(allResults).filter(r => r && r.scanType);
      
      if (scanResults.length === 0) {
        return {
          success: false,
          error: 'No scan results available for OWASP vulnerability analysis. For GitHub repositories, code analysis will be performed automatically.',
          scanType: 'owasp-vulnerabilities',
          target: targetUrl,
          timestamp: new Date().toISOString()
        };
      }

      // Perform OWASP Top 10 vulnerability mapping for scan results
      const owaspAnalysis = this.mapToOwaspTop10(scanResults, targetUrl);
      
      return {
        success: true,
        data: owaspAnalysis,
        scanType: 'owasp-vulnerabilities',
        target: targetUrl,
        timestamp: new Date().toISOString()
      };
    } catch (error) {
      console.error('OWASP vulnerability analysis failed:', error.message);
      return {
        success: false,
        error: error.message,
        scanType: 'owasp-vulnerabilities',
        target: targetUrl,
        timestamp: new Date().toISOString()
      };
    }
  }

  /**
   * Execute flow chart generation
   * @param {string} targetUrl - Target URL
   * @param {Object} nodeData - Node configuration data
   * @param {Object} allResults - All scan results so far
   * @returns {Promise<Object>} Flow chart result
   */
  async executeFlowChartGeneration(targetUrl, nodeData = {}, allResults = {}) {
    try {
      console.log(`Generating flow chart for workflow execution`);
      
      // Generate workflow execution flow chart
      const flowChart = this.generateWorkflowFlowChart(allResults, targetUrl);
      
      return {
        success: true,
        data: flowChart,
        scanType: 'flow-chart',
        target: targetUrl,
        timestamp: new Date().toISOString()
      };
    } catch (error) {
      console.error('Flow chart generation failed:', error.message);
      return {
        success: false,
        error: error.message,
        scanType: 'flow-chart',
        target: targetUrl,
        timestamp: new Date().toISOString()
      };
    }
  }

  /**
   * Map scan results to OWASP Top 10 vulnerabilities
   * @param {Array} scanResults - Array of scan results
   * @param {string} targetUrl - Target URL
   * @returns {Object} OWASP analysis
   */
  mapToOwaspTop10(scanResults, targetUrl) {
    const owaspTop10 = {
      'A01:2021 – Broken Access Control': [],
      'A02:2021 – Cryptographic Failures': [],
      'A03:2021 – Injection': [],
      'A04:2021 – Insecure Design': [],
      'A05:2021 – Security Misconfiguration': [],
      'A06:2021 – Vulnerable and Outdated Components': [],
      'A07:2021 – Identification and Authentication Failures': [],
      'A08:2021 – Software and Data Integrity Failures': [],
      'A09:2021 – Security Logging and Monitoring Failures': [],
      'A10:2021 – Server-Side Request Forgery': []
    };

    const findings = [];

    scanResults.forEach(result => {
      if (result.success && result.data) {
        switch (result.scanType) {
          case 'nmap':
            // Map nmap findings to OWASP categories
            if (result.data.open_ports) {
              findings.push({
                category: 'A05:2021 – Security Misconfiguration',
                finding: `Open ports detected: ${result.data.open_ports.join(', ')}`,
                severity: 'Medium',
                source: 'Nmap'
              });
            }
            break;

          case 'gobuster':
            // Map gobuster findings to OWASP categories
            if (result.data.directories_found) {
              findings.push({
                category: 'A01:2021 – Broken Access Control',
                finding: `Exposed directories found: ${result.data.directories_found.length} directories`,
                severity: 'Medium',
                source: 'Gobuster'
              });
            }
            break;

          case 'nikto':
            // Map Nikto findings to OWASP categories
            if (result.data.nikto && result.data.nikto.vulnerabilities_found) {
              findings.push({
                category: 'A05:2021 – Security Misconfiguration',
                finding: `Web vulnerabilities detected: ${result.data.nikto.vulnerabilities.length} issues`,
                severity: 'Medium',
                source: 'Nikto'
              });
            }
            break;

          case 'sqlmap':
            // Map SQLMap findings to OWASP categories
            if (result.data.vulnerabilities_found) {
              findings.push({
                category: 'A03:2021 – Injection',
                finding: 'SQL injection vulnerabilities detected',
                severity: 'High',
                source: 'SQLMap'
              });
            }
            break;

          case 'wpscan':
            // Map WPScan findings to OWASP categories
            if (result.data.vulnerabilities) {
              findings.push({
                category: 'A06:2021 – Vulnerable and Outdated Components',
                finding: 'WordPress vulnerabilities detected',
                severity: 'High',
                source: 'WPScan'
              });
            }
            break;
        }
      }
    });

    // Group findings by OWASP category
    findings.forEach(finding => {
      if (owaspTop10[finding.category]) {
        owaspTop10[finding.category].push(finding);
      }
    });

    return {
      target: targetUrl,
      analysis_date: new Date().toISOString(),
      owasp_categories: owaspTop10,
      total_findings: findings.length,
      high_severity: findings.filter(f => f.severity === 'High').length,
      medium_severity: findings.filter(f => f.severity === 'Medium').length,
      low_severity: findings.filter(f => f.severity === 'Low').length
    };
  }

  /**
   * Generate workflow execution flow chart
   * @param {Object} allResults - All execution results
   * @param {string} targetUrl - Target URL
   * @returns {Object} Flow chart data
   */
  generateWorkflowFlowChart(allResults, targetUrl) {
    const nodes = [];
    const edges = [];
    
    let yPosition = 0;
    const nodeSpacing = 100;

    // Generate flow chart nodes based on execution results
    Object.entries(allResults).forEach(([nodeId, result], index) => {
      const nodeType = result.type || result.scanType || 'unknown';
      
      nodes.push({
        id: nodeId,
        type: nodeType,
        position: { x: 200, y: yPosition },
        data: {
          label: `${nodeType.toUpperCase()}`,
          status: result.success ? 'success' : 'failed',
          timestamp: result.timestamp,
          details: result.success ? 'Completed' : (result.error || 'Failed')
        }
      });

      // Add edge to next node (simple linear flow for now)
      if (index > 0) {
        const previousNodeId = Object.keys(allResults)[index - 1];
        edges.push({
          id: `edge-${previousNodeId}-${nodeId}`,
          source: previousNodeId,
          target: nodeId,
          type: 'smoothstep'
        });
      }

      yPosition += nodeSpacing;
    });

    return {
      target: targetUrl,
      generated_at: new Date().toISOString(),
      flow_chart: {
        nodes,
        edges,
        metadata: {
          total_nodes: nodes.length,
          successful_nodes: nodes.filter(n => n.data.status === 'success').length,
          failed_nodes: nodes.filter(n => n.data.status === 'failed').length
        }
      }
    };
  }

  /**
   * Check if target URL is a GitHub repository
   * @param {string} targetUrl - Target URL to check
   * @returns {boolean} True if GitHub repository
   */
  isGitHubRepository(targetUrl) {
    return targetUrl.includes('github.com') && 
           (targetUrl.includes('/') || targetUrl.match(/github\.com\/[\w-]+\/[\w-]+/));
  }

  /**
   * Perform OWASP code analysis on GitHub repository
   * @param {string} githubUrl - GitHub repository URL
   * @returns {Promise<Object>} Analysis result
   */
  async performGitHubCodeAnalysis(githubUrl) {
    try {
      const githubService = require('./githubService');
      const analysisService = require('./analysisService');
      
      console.log(`Fetching code from GitHub repository: ${githubUrl}`);
      
      // Extract owner and repo from URL
      const urlParts = githubUrl.replace('https://github.com/', '').split('/');
      const owner = urlParts[0];
      const repo = urlParts[1];
      
      if (!owner || !repo) {
        throw new Error('Invalid GitHub repository URL format');
      }
      
      console.log(`Analyzing repository: ${owner}/${repo}`);
      
      // Fetch repository files
      const repoFiles = await githubService.getRepositoryFiles(owner, repo);
      
      if (!repoFiles || repoFiles.length === 0) {
        throw new Error('No files found in repository or repository is private/inaccessible');
      }
      
      console.log(`Found ${repoFiles.length} files, performing OWASP security analysis...`);
      
      // Perform security analysis using the existing analysis service
      const owaspQuery = `Analyze this code for OWASP Top 10 2021 security vulnerabilities. Return ONLY actual security vulnerabilities found, not general code quality issues.

Focus specifically on:
- A01: Broken Access Control (unauthorized access, privilege escalation)
- A02: Cryptographic Failures (weak encryption, exposed secrets)
- A03: Injection (SQL, NoSQL, LDAP, OS command injection)
- A04: Insecure Design (design flaws, threat modeling gaps)
- A05: Security Misconfiguration (default configs, exposed endpoints)
- A06: Vulnerable Components (outdated libraries, known CVEs)
- A07: Authentication Failures (weak passwords, session management)
- A08: Data Integrity Failures (insecure deserialization)
- A09: Logging Failures (insufficient monitoring)
- A10: Server-Side Request Forgery (SSRF attacks)

Response format:
{
  "summary": "Brief analysis summary",
  "security_vulnerabilities": [
    {
      "category": "OWASP category (A01-A10)",
      "description": "Specific vulnerability found",
      "severity": "High|Medium|Low",
      "location": "File/function where found"
    }
  ],
  "code_quality_issues": [
    "Non-security related issues (performance, style, etc.)"
  ]
}

If NO security vulnerabilities are found, return empty security_vulnerabilities array. Distinguish between actual security risks and code quality/style issues.`;
      
      const analysisResult = await analysisService.getQueryAboutCode(repoFiles, owaspQuery);
      
      // Parse the analysis result
      let parsedAnalysis;
      try {
        parsedAnalysis = JSON.parse(analysisResult);
      } catch (e) {
        // If parsing fails, create a structured response
        parsedAnalysis = {
          summary: "OWASP security analysis completed",
          security_vulnerabilities: [],
          code_quality_issues: [analysisResult],
          analysis_type: "text_based"
        };
      }
      
      // Format the result in OWASP structure
      const owaspResult = this.formatCodeAnalysisToOwasp(parsedAnalysis, githubUrl, repoFiles.length);
      
      return {
        success: true,
        data: owaspResult,
        scanType: 'owasp-vulnerabilities',
        target: githubUrl,
        timestamp: new Date().toISOString()
      };
      
    } catch (error) {
      console.error('GitHub code analysis failed:', error.message);
      return {
        success: false,
        error: `GitHub repository analysis failed: ${error.message}`,
        scanType: 'owasp-vulnerabilities',
        target: githubUrl,
        timestamp: new Date().toISOString()
      };
    }
  }

  /**
   * Format code analysis result to OWASP structure
   * @param {Object} analysisResult - Analysis result from AI
   * @param {string} githubUrl - GitHub repository URL
   * @param {number} fileCount - Number of files analyzed
   * @returns {Object} OWASP formatted result
   */
  formatCodeAnalysisToOwasp(analysisResult, githubUrl, fileCount) {
    const owaspCategories = {
      'A01:2021 – Broken Access Control': [],
      'A02:2021 – Cryptographic Failures': [],
      'A03:2021 – Injection': [],
      'A04:2021 – Insecure Design': [],
      'A05:2021 – Security Misconfiguration': [],
      'A06:2021 – Vulnerable and Outdated Components': [],
      'A07:2021 – Identification and Authentication Failures': [],
      'A08:2021 – Software and Data Integrity Failures': [],
      'A09:2021 – Security Logging and Monitoring Failures': [],
      'A10:2021 – Server-Side Request Forgery': []
    };

    const findings = [];

    // Extract security vulnerabilities and code quality issues
    const securityVulns = analysisResult.security_vulnerabilities || [];
    const codeQualityIssues = analysisResult.code_quality_issues || [];
    const summary = analysisResult.summary || '';
    
    // Process actual security vulnerabilities
    securityVulns.forEach((vuln, index) => {
      if (vuln.category && vuln.description) {
        findings.push({
          category: vuln.category,
          finding: vuln.description,
          severity: vuln.severity || 'Medium',
          source: 'Code Analysis',
          location: vuln.location || `Vulnerability ${index + 1}`,
          issue_type: 'Security'
        });
      }
    });

    // Process code quality issues (assign low severity, not security related)
    codeQualityIssues.forEach((issue, index) => {
      if (typeof issue === 'string' && issue.trim()) {
        findings.push({
          category: 'A04:2021 – Insecure Design',
          finding: issue,
          severity: 'Low',
          source: 'Code Analysis',
          line_reference: `Code Quality Issue ${index + 1}`,
          issue_type: 'Code Quality'
        });
      }
    });

    // Handle legacy format (for backward compatibility)
    const legacyIssues = analysisResult.potential_issues || [];
    if (legacyIssues.length > 0 && securityVulns.length === 0) {
      // Map common security issues to OWASP categories
      const securityKeywords = {
        'injection': 'A03:2021 – Injection',
        'sql injection': 'A03:2021 – Injection',
        'xss': 'A03:2021 – Injection',
        'cross-site scripting': 'A03:2021 – Injection',
        'authentication': 'A07:2021 – Identification and Authentication Failures',
        'authorization': 'A01:2021 – Broken Access Control',
        'access control': 'A01:2021 – Broken Access Control',
        'cryptographic': 'A02:2021 – Cryptographic Failures',
        'encryption': 'A02:2021 – Cryptographic Failures',
        'password': 'A02:2021 – Cryptographic Failures',
        'configuration': 'A05:2021 – Security Misconfiguration',
        'misconfiguration': 'A05:2021 – Security Misconfiguration',
        'vulnerability': 'A06:2021 – Vulnerable and Outdated Components',
        'outdated': 'A06:2021 – Vulnerable and Outdated Components',
        'logging': 'A09:2021 – Security Logging and Monitoring Failures',
        'monitoring': 'A09:2021 – Security Logging and Monitoring Failures',
        'deserialization': 'A08:2021 – Software and Data Integrity Failures',
        'ssrf': 'A10:2021 – Server-Side Request Forgery',
        'server-side request': 'A10:2021 – Server-Side Request Forgery'
      };

      legacyIssues.forEach((issue, index) => {
        const issueText = issue.toLowerCase();
        
        // Skip findings that explicitly say "None found"
        const isNoneFound = issueText.includes('none found') || 
                           issueText.includes('no vulnerabilities') ||
                           issueText.includes('not found') ||
                           issueText.includes('no issues') ||
                           issueText.includes('none detected');

        if (isNoneFound) {
          return; // Skip this finding entirely
        }

        let category = 'A04:2021 – Insecure Design';
        let severity = 'Low';

        // Check for actual security issues
        const isActualSecurityIssue = issueText.includes('vulnerability') ||
                                     issueText.includes('security risk') ||
                                     issueText.includes('exploit') ||
                                     issueText.includes('attack') ||
                                     issueText.includes('malicious') ||
                                     issueText.includes('unauthorized') ||
                                     issueText.includes('breach');

        // Find matching OWASP category
        for (const [keyword, owaspCategory] of Object.entries(securityKeywords)) {
          if (issueText.includes(keyword)) {
            category = owaspCategory;
            if (isActualSecurityIssue && (keyword.includes('injection') || keyword.includes('authentication'))) {
              severity = 'High';
            } else if (isActualSecurityIssue) {
              severity = 'Medium';
            }
            break;
          }
        }

        findings.push({
          category: category,
          finding: issue,
          severity: severity,
          source: 'Code Analysis',
          line_reference: `Issue ${index + 1}`,
          issue_type: isActualSecurityIssue ? 'Security' : 'Code Quality'
        });
      });
    }

    // If no specific issues found, create a general finding based on summary
    if (findings.length === 0 && summary) {
      findings.push({
        category: 'A04:2021 – Insecure Design',
        finding: 'Code analysis completed - review summary for potential security considerations',
        severity: 'Low',
        source: 'Code Analysis',
        details: summary
      });
    }

    // Group findings by category
    findings.forEach(finding => {
      if (owaspCategories[finding.category]) {
        owaspCategories[finding.category].push(finding);
      }
    });

    return {
      target: githubUrl,
      analysis_type: 'github_code_analysis',
      files_analyzed: fileCount,
      analysis_date: new Date().toISOString(),
      owasp_categories: owaspCategories,
      summary: analysisResult.summary || 'OWASP security analysis completed',
      total_findings: findings.length,
      high_severity: findings.filter(f => f.severity === 'High').length,
      medium_severity: findings.filter(f => f.severity === 'Medium').length,
      low_severity: findings.filter(f => f.severity === 'Low').length,
      analysis_metadata: {
        repository: githubUrl,
        scan_type: 'Static Code Analysis',
        owasp_version: '2021'
      }
    };
  }

  /**
   * Generate AI-powered report from workflow results
   * @param {Object} results - Complete workflow execution results
   * @param {string} targetUrl - Target URL that was scanned
   * @returns {Promise<Object>} AI-generated report
   */
  async generateAIReport(results, targetUrl) {
    try {
      console.log(`Generating AI report for ${targetUrl}`);

      // Prepare scan results for AI analysis
      const scanResults = {};
      
      // Extract results from each scan node
      for (const [nodeId, nodeResult] of Object.entries(results)) {
        console.log(`Processing node ${nodeId}:`, nodeResult);
        
        if (nodeResult.success && nodeResult.data) {
          // Map node types to scan result format - be more flexible with type detection
          const nodeType = nodeResult.type || 'unknown';
          const nodeName = nodeId.toLowerCase();
          
          console.log(`Node type: ${nodeType}, Node ID: ${nodeId}`);
          
          // Check both the type and the node ID/name for better matching
          if (nodeType === 'gobuster' || nodeName.includes('gobuster')) {
            scanResults.gobuster_scan = nodeResult.data;
            console.log('Added gobuster scan results');
          } else if (nodeType === 'nmap' || nodeName.includes('nmap')) {
            scanResults.nmap_scan = nodeResult.data;
            console.log('Added nmap scan results');
          } else if (nodeType === 'nikto' || nodeName.includes('nikto')) {
            scanResults.nikto_scan = nodeResult.data;
            console.log('Added nikto scan results');
          } else if (nodeType === 'sqlmap' || nodeName.includes('sqlmap')) {
            scanResults.sqlmap_scan = nodeResult.data;
            console.log('Added sqlmap scan results');
          } else if (nodeType === 'wpscan' || nodeName.includes('wpscan')) {
            scanResults.wpscan_scan = nodeResult.data;
            console.log('Added wpscan scan results');
          } else {
            // Add all scan results, even unknown types
            scanResults[nodeType] = nodeResult.data;
            console.log(`Added ${nodeType} scan results`);
          }
        } else {
          console.log(`Skipping node ${nodeId}: success=${nodeResult.success}, hasData=${!!nodeResult.data}`);
        }
      }

      console.log('Scan results prepared:', Object.keys(scanResults));
      console.log('Full scan results being sent to Django:', JSON.stringify(scanResults, null, 2));

      // Call Django AI report generation endpoint
      const reportResponse = await djangoScanService.callDjangoEndpoint(
        '/api/nmap/report/generate/',
        {
          scan_results: scanResults,
          target_url: targetUrl
        }
      );

      if (reportResponse.success) {
        console.log('AI report generated successfully');
        return {
          success: true,
          report: reportResponse.data,
          type: 'ai_report'
        };
      } else {
        console.error('Failed to generate AI report:', reportResponse.error);
        return {
          success: false,
          error: reportResponse.error,
          type: 'ai_report'
        };
      }
    } catch (error) {
      console.error('Error generating AI report:', error);
      return {
        success: false,
        error: error.message,
        type: 'ai_report'
      };
    }
  }
}

module.exports = new WorkflowExecutionService();
