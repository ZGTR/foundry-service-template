# The estate's caller group on the service's tasks, in both directions.
#
# Run by `scripts/check_service_iac.sh` against a MOCK provider: nothing here reaches
# an account, and `apply` below writes only the test's in-memory state. It is `apply`
# rather than `plan` because the service's own group id is not known until its group
# exists, and a set that holds an unknown has no length to assert on.
#
# Every expected value is a literal written here. None is read back from the
# configuration under test.

mock_provider "aws" {
  mock_resource "aws_lb_target_group" {
    defaults = {
      arn = "arn:aws:elasticloadbalancing:eu-west-1:123456789012:targetgroup/x/0123456789abcdef"
    }
  }
  mock_resource "aws_iam_role" {
    defaults = {
      arn = "arn:aws:iam::123456789012:role/x"
    }
  }
  mock_resource "aws_ecs_task_definition" {
    defaults = {
      arn = "arn:aws:ecs:eu-west-1:123456789012:task-definition/x:1"
    }
  }
  mock_data "aws_iam_policy_document" {
    defaults = {
      json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}"
    }
  }
}

# The service's own group has a known id, and ONLY that group. A default for every
# `aws_security_group` would hand a second group the same id, and two groups with one
# id are one member of the set the runs below compare: a group added to the tasks would
# pass. Every other group keeps the id the mock provider generates for it.
override_resource {
  target = aws_security_group.service
  values = {
    id = "sg-0a11a11a11a11a11a"
  }
}

variables {
  environment            = "dev"
  aws_region             = "eu-west-1"
  vpc_id                 = "vpc-00000000"
  image_uri              = "123456789012.dkr.ecr.eu-west-1.amazonaws.com/x@sha256:0000000000000000000000000000000000000000000000000000000000000000"
  exposure               = "private"
  internal_listener_arn  = "arn:aws:elasticloadbalancing:eu-west-1:123456789012:listener/app/internal/0123456789abcdef/0123456789abcdef"
  internal_alb_sg_id     = "sg-00000000"
  internal_zone_suffix   = "svc.example.internal"
  listener_rule_priority = 100
  ready_path             = "/health/ready"
  sqs_queue_arn          = "arn:aws:sqs:eu-west-1:123456789012:q" # COMMS-FLAVOUR-STRIP-LINE: the variable is stripped with the queue
}

run "unset_leaves_only_the_services_own_group" {
  command = apply

  assert {
    condition     = aws_ecs_service.this.network_configuration[0].security_groups == toset(["sg-0a11a11a11a11a11a"])
    error_message = "with estate_caller_sg_id unset the tasks must carry exactly their own group"
  }
}

# What the variable accepts: `sg-` and 8 hex digits (the older id format) or 17 (the
# current one), and no other length. The two accepting runs assert nothing, because a
# refused value fails the plan and a failed plan fails the run.
#
# These runs sit before the two that set the variable on purpose. A failed run makes
# every later run a skip, so each length is tried here by the run named for it.

run "an_eight_digit_id_is_accepted" {
  command = plan

  variables {
    estate_caller_sg_id = "sg-0c0ffee0"
  }
}

run "a_seventeen_digit_id_is_accepted" {
  command = plan

  variables {
    estate_caller_sg_id = "sg-0c0ffee0c0ffee0c0"
  }
}

run "the_bare_prefix_is_refused" {
  command = plan

  variables {
    estate_caller_sg_id = "sg-"
  }

  expect_failures = [var.estate_caller_sg_id]
}

run "one_hex_digit_is_refused" {
  command = plan

  variables {
    estate_caller_sg_id = "sg-a"
  }

  expect_failures = [var.estate_caller_sg_id]
}

run "a_twelve_digit_id_is_refused" {
  command = plan

  variables {
    estate_caller_sg_id = "sg-0c0ffee0c0ff"
  }

  expect_failures = [var.estate_caller_sg_id]
}

run "a_forty_digit_id_is_refused" {
  command = plan

  variables {
    estate_caller_sg_id = "sg-0123456789abcdef0123456789abcdef01234567"
  }

  expect_failures = [var.estate_caller_sg_id]
}

run "a_group_name_is_refused" {
  command = plan

  variables {
    estate_caller_sg_id = "estate-callers"
  }

  expect_failures = [var.estate_caller_sg_id]
}

run "a_list_of_groups_is_refused" {
  command = plan

  variables {
    estate_caller_sg_id = "sg-0c0ffee0c0ffee0c0,sg-0badc0de0badc0de0"
  }

  expect_failures = [var.estate_caller_sg_id]
}

run "set_adds_the_caller_group_beside_the_services_own" {
  command = apply

  variables {
    estate_caller_sg_id = "sg-0c0ffee0c0ffee0c0"
  }

  assert {
    condition     = aws_ecs_service.this.network_configuration[0].security_groups == toset(["sg-0a11a11a11a11a11a", "sg-0c0ffee0c0ffee0c0"])
    error_message = "with estate_caller_sg_id set the tasks must carry their own group and the caller group"
  }
}

# The inline rules only. A `terraform test` cannot enumerate the standalone rule
# resources, in this root or another, so tests/shape/test_private_ingress.py holds the
# rest of this sentence: it reads every Terraform file under deploy/iac.
run "the_caller_group_admits_nothing_into_the_service" {
  command = apply

  variables {
    estate_caller_sg_id = "sg-0c0ffee0c0ffee0c0"
  }

  assert {
    condition     = [for rule in aws_security_group.service.ingress : rule.security_groups] == [toset(["sg-00000000"])]
    error_message = "the service's inline rules must admit only the internal load balancer's group, whatever the caller group is"
  }
}
