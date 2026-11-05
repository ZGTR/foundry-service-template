# The DURABLE root. Everything declared here outlives any single release of the
# service, so it has its own state, its own apply cadence, and its own blast
# radius. The application root next door (../app) never declares one of these
# and never reads this root's remote state -- it consumes the published outputs
# in outputs.tf as ordinary input variables.
#
# WHY THE SPLIT IS HERE AND NOT IN EACH SERVICE. Existing services with mixed
# roots were split in place, with no new repository. Putting the same split in the TEMPLATE means
# every service emitted after this one is born split, so there is nothing to
# retrofit next time.
#
# A service that owns no durable state leaves this root as it ships: it declares
# no resources, validates clean, and publishes an empty contract. That is a
# legitimate steady state, not a stub to be filled in.

terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}
