#!/usr/bin/env python3
"""
Test runner for the Any.do API client library.

This script runs all tests and provides a summary of results.
"""

import unittest
import sys
import os
from io import StringIO


def run_all_tests():
    """Run all test modules and return results."""
    # Discover and run all tests
    loader = unittest.TestLoader()
    start_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tests')
    suite = loader.discover(start_dir, pattern='test_*.py')
    
    # Run tests with detailed output
    stream = StringIO()
    runner = unittest.TextTestRunner(stream=stream, verbosity=2)
    result = runner.run(suite)
    
    # Print results
    output = stream.getvalue()
    print(output)
    
    # Print summary
    print("\n" + "="*60)
    print("TEST SUMMARY")
    print("="*60)
    print(f"Tests run: {result.testsRun}")
    print(f"Failures: {len(result.failures)}")
    print(f"Errors: {len(result.errors)}")
    print(f"Skipped: {len(result.skipped)}")
    
    if result.failures:
        print("\nFAILURES:")
        for test, traceback in result.failures:
            print(f"- {test}: {traceback.split('AssertionError: ')[-1].split('\\n')[0]}")
    
    if result.errors:
        print("\nERRORS:")
        for test, traceback in result.errors:
            print(f"- {test}: {traceback.split('\\n')[-2]}")
    
    # Return success status
    return len(result.failures) == 0 and len(result.errors) == 0


def run_specific_test(test_module):
    """Run a specific test module."""
    try:
        # Import the test module
        module = __import__(test_module)
        
        # Create test suite from the module
        loader = unittest.TestLoader()
        suite = loader.loadTestsFromModule(module)
        
        # Run tests
        runner = unittest.TextTestRunner(verbosity=2)
        result = runner.run(suite)
        
        return len(result.failures) == 0 and len(result.errors) == 0
        
    except ImportError as e:
        print(f"Error importing test module '{test_module}': {e}")
        return False


def main():
    """Main function."""
    print("Any.do API Client Test Runner")
    print("="*40)
    
    if len(sys.argv) > 1:
        # Run specific test module
        test_module = sys.argv[1]
        if not test_module.startswith('test_'):
            test_module = f'test_{test_module}'
        if test_module.endswith('.py'):
            test_module = test_module[:-3]
            
        print(f"Running tests from module: {test_module}")
        success = run_specific_test(test_module)
    else:
        # Run all tests
        print("Running all tests...")
        success = run_all_tests()
    
    # Exit with appropriate code
    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main() 
